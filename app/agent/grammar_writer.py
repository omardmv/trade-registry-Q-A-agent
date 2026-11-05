"""Grammar planner mode of the writer (PLANNER=grammar): one generic plan instead of the structured form.

Same loop, grounding, deadline and free-SQL fallback as the legacy Writer; the compile tool is run_plan(plan).
"""
import logging
import re

from app import config
from app.agent.guard import _tok_match
from app.agent.validators import grounding_status, missing_message
from app.agent.writer import Writer, WriterResult
from app.db import QueryError
from app.plan.build import build_sql
from app.plan.checks import apply_defaults, consistency_error, plan_missing_condition_error
from app.plan.dictionary import Dictionary, load_dictionary
from app.plan.explain import explain_plan
from app.plan import wordclass
from app.plan.judge import judge, term_for
from app.plan.mentions import Resolution, mention_error
from app.plan.model import PlanError, parse_plan
from app.plan.prompt import TOOLS, build_system, load_examples
from app.plan.validate import grounding_pairs, validate_plan
from app.plan.vocab import Vocab, build_vocab, log_refusal
from app.text import detect_lang, normalize_text

_NAMED_GROUNDS = ("company", "broker", "product", "product_family", "product_variant", "region")
_SQL_STRING = re.compile(r"'((?:[^']|'')*)'")
_CROSS_KINDS = ("company", "broker", "product")
_CROSS_MIN_SCORE = 90.0

log = logging.getLogger(__name__)


def plan_args(args: dict) -> dict:
    """The plan of a run_plan call: its arguments (flat schema, one nesting level less for small models), or a
    nested {plan: {...}} as older prompts had it; assumptions are not part of the plan."""
    if isinstance(args.get("plan"), dict):
        plan = dict(args["plan"])
    else:
        plan = {k: v for k, v in args.items() if k != "plan"}
    plan.pop("assumptions", None)
    return plan


class GrammarWriter(Writer):
    tools = TOOLS
    compile_tool = "run_plan"
    nudge = "Do not answer in text. Call run_plan, submit_sql or declare_unanswerable."

    def __init__(self, llm, db, resolver, max_tool_calls: int = 8, max_fixes: int = 2,
                 llm_timeout_s: float = config.LLM_TIMEOUT_S, catalog: dict[str, set[str]] | None = None,
                 dictionary: Dictionary | None = None, examples: list[dict] | None = None,
                 vocab: Vocab | None = None, use_judge: bool | None = None):
        super().__init__(llm, db, resolver, None, "", max_tool_calls, max_fixes, llm_timeout_s, catalog)
        self.dictionary = dictionary or load_dictionary()
        self.system = build_system(self.dictionary, load_examples() if examples is None else examples)
        # closed vocabulary (stage 2 here; stage 1 runs in the pipeline before any LLM call)
        self.vocab = vocab if vocab is not None else build_vocab(self.catalog, resolver)
        # stage 2 judge (app/plan/judge.py); off: a word the plan does not use refuses (strict stage 2)
        self.use_judge = config.JUDGE if use_judge is None else use_judge
        # max safety (app.config.SAFETY): unknown plan words refuse, the judge confirms every answer, no free SQL
        self.safety = config.SAFETY

    def _plan_names(self, plan) -> list[str]:
        """Names of the entities the plan uses: its filter values (incl. share numerators) by their ground."""
        s = self.dictionary.subject(plan.subject)
        out = []
        for f in list(plan.filters) + [w for m in plan.measures for w in m.where]:
            fd = s.fields.get(f.field)
            if fd is None:
                continue
            values = f.value if isinstance(f.value, tuple) else (f.value,)
            for v in values:
                if fd.ground in _NAMED_GROUNDS:
                    out += self.resolver.entity_names(fd.ground, str(v))
                elif isinstance(v, str):
                    out.append(v)
        return out

    def _vocab_gate(self, question: str, names, calculation: str, deadline: float, trace=None,
                    plan=None) -> WriterResult | None:
        """Stage 2 of the closed vocabulary, after the plan passed every check: question words outside the lexicon
        that are not words of an entity the query uses go to the judge with the calculation in words; the judge
        says whether the calculation still answers the question. Not covered -> unanswerable (not a fix: the
        model must not relink the word to a nearby fact); no verdict -> failed (refuse, never answer). With the
        judge off any such word refuses. Max safety: such a word that can be a noun refuses (app/plan/wordkind.py);
        the judge runs on every plan, with the other unknown words (wording). With the word classifier
        (app/plan/wordclass.py) a word labelled at stage 1 does not block: a concept word must be used by the plan,
        and every labelled word goes to the judge."""
        if self.vocab is None:
            return None
        if self.safety == "max":
            # unknown words that can be nouns refuse; the other unknown words (wording) go to the judge, which
            # confirms every answer
            gate = self.vocab.gate_in_plan(question, names)
            classes = wordclass.CURRENT.get()
            blocking, wording = list(gate.blocking), [u.word for u in gate.wording]
            if classes is not None:
                # word classifier (app/plan/wordclass.py): a word labelled at stage 1 is wording or a concept the
                # plan must use; a word stage 1 did not label (a name word of an entity the plan does not use) keeps
                # the noun gate
                labelled = [u for u in blocking if classes.label_of(u.word) is not None]
                blocking = [u for u in blocking if classes.label_of(u.word) is None]
                wording += [u.word for u in labelled]
                missing = wordclass.unused_concepts(classes, wording, plan, self.dictionary) if plan is not None \
                    else [(w, classes.label_of(w)) for w in wording if classes.label_of(w) != wordclass.WORDING]
                if trace is not None and (labelled or wording):
                    trace.append({"tool": "concept_check", "args": {"words": wording},
                                  "unused": [f"{w} -> {c}" for w, c in missing]})
                if missing and not blocking:
                    w, c = missing[0]
                    log.info("word classifier (stage 2): «%s» means %s, the plan does not use it | %s", w, c, question)
                    return WriterResult("unanswerable", reason=f"word classifier: «{w}» means {c}, not in the plan",
                                        term=w)
            if blocking:
                log_refusal("stage 2, max safety", question, blocking)
                blocked = ", ".join(u.word for u in blocking)
                return WriterResult("unanswerable", reason="not in the data: " + blocked, term=blocking[0].word)
            verdict = judge(self.llm, question, calculation, wording, deadline, self.llm_timeout_s)
            if trace is not None:
                trace.append({"tool": "judge", "args": {"words": wording, "calculation": calculation},
                              "covered": verdict.covered, "missing": verdict.missing, "error": verdict.error})
            if verdict.covered is True:
                return None
            if verdict.covered is None:
                return WriterResult("failed", reason=f"judge: {verdict.error}")
            log.info("judge (max safety): not covered: %s | %s", verdict.missing, question)
            return WriterResult("unanswerable", reason=f"judge: not covered: {verdict.missing}",
                                term=term_for(verdict, question, wording))
        unknown = self.vocab.unknown_in_plan(question, names)
        words = [u.word for u in unknown]
        if not unknown:
            return None
        if not self.use_judge:
            log_refusal("stage 2", question, unknown)
            return WriterResult("unanswerable", reason="not in the data: " + ", ".join(words), term=unknown[0].word)
        verdict = judge(self.llm, question, calculation, words, deadline, self.llm_timeout_s)
        if trace is not None:
            trace.append({"tool": "judge", "args": {"words": words, "calculation": calculation},
                          "covered": verdict.covered, "missing": verdict.missing, "error": verdict.error})
        if verdict.covered is True:
            return None
        if verdict.covered is None:
            return WriterResult("failed", reason=f"judge: {verdict.error}")
        log_refusal("stage 2 judge", question, unknown)
        return WriterResult("unanswerable", reason=f"judge: not covered: {verdict.missing or ', '.join(words)}",
                            term=term_for(verdict, question, words))

    def _compile(self, args, allowed, question, deadline, names=(), state=None) -> WriterResult | str:
        d = self.dictionary
        try:
            plan = validate_plan(parse_plan(plan_args(args)), d, self.catalog)
        except (PlanError, KeyError, TypeError) as e:
            return str(e)
        raw_filters = frozenset(f.field for f in plan.filters)
        plan = apply_defaults(plan, d, question)
        if (err := consistency_error(plan, d, question)) is not None:
            return err
        if (err := plan_missing_condition_error(question, plan, d, self._guard_index, names,
                                                self._product_attrs)) is not None:
            if state is not None:
                state["guard"] = state.get("guard") or err      # submit_sql may not bypass it
            return err
        statuses = {(k, v): grounding_status(v, k, allowed, question, self._exists) for k, v in grounding_pairs(plan, d)}
        absent = sorted(v for (k, v), st in statuses.items() if st == "missing")
        if absent:
            return "; ".join(missing_message(v) for v in absent)
        missing = sorted(v for (k, v), st in statuses.items() if st != "ok")
        if missing:
            return f"values {missing} were not returned by resolve() in this request; call resolve() first"
        resolutions = (state or {}).get("resolved", ())
        if (err := mention_error(plan, d, question, self._guard_index, resolutions, names, raw_filters)) is not None:
            return err
        try:
            built = build_sql(plan, d, variants=self.resolver.variant_names)
        except (PlanError, KeyError, ValueError, TypeError) as e:
            return f"could not build the plan: {e}"
        calculation = explain_plan(plan, d, built.notes, detect_lang(question))
        if (gate := self._vocab_gate(question, self._plan_names(plan), calculation, deadline,
                                     (state or {}).get("trace"), plan)) is not None:
            return gate
        try:
            result = self.db.query(built.sql, timeout_s=self._timeout(deadline, 10.0))
        except (PlanError, KeyError, ValueError, TypeError) as e:
            return f"could not build the plan: {e}"
        except QueryError as e:
            return f"query failed: {e}"
        return WriterResult("ok", "compiled", built.sql, result, plan, built.splits, built.notes,
                            [str(a) for a in args.get("assumptions") or (args.get("plan") if isinstance(args.get("plan"), dict)
                                                                    else {}).get("assumptions") or []],
                            measure_columns=built.measure_columns)

    def _sql(self, args, allowed, question, deadline, names=(), state=None) -> WriterResult | str:
        if self.safety == "max":
            return ("free SQL is turned off: express the question with run_plan, or call declare_unanswerable if "
                    "the plan cannot express it")
        sql = str(args.get("sql", ""))
        used = [n for kind, value in allowed if value and str(value) in sql
                for n in self.resolver.entity_names({"inn": "company"}.get(kind, kind), str(value))]
        used += [m.replace("''", "'") for m in _SQL_STRING.findall(sql)]
        out = super()._sql(args, allowed, question, deadline, names, state)
        if isinstance(out, WriterResult) and out.status == "ok":
            # free SQL has no plan explanation: the judge reads the query itself
            if (gate := self._vocab_gate(question, used, f"SQL: {out.sql}", deadline,
                                         (state or {}).get("trace"))) is not None:
                return gate
        return out

    def _tool(self, name, args, allowed, question, deadline, names=None, state=None) -> dict:
        if name == "resolve" and "kind" not in args and "object" in args:
            args = {**args, "kind": args["object"]}       # small models sometimes name the key "object"
        if name == "resolve" and str(args.get("kind")) == "catalog":
            return self._resolve_catalog(str(args.get("text", "")))
        out = super()._tool(name, args, allowed, question, deadline, names, state)
        if name == "resolve" and not out.get("candidates") and str(args.get("kind")) in _CROSS_KINDS:
            # «Tumizo Neft» looked up as a product: say which other kind the name is, instead of a dead end
            other = self._other_kind(str(args.get("kind")), str(args.get("text", "")))
            if other:
                out = {**out, "note": other}
        cands = out.get("candidates") or []
        if name == "resolve" and str(args.get("kind")) in ("company", "broker") and len(cands) >= 2 \
                and cands[0].get("score") == cands[1].get("score"):
            # «Moto Group»: two registered companies with the same name; show both, do not refuse
            same = [c.get("id") for c in cands if c.get("score") == cands[0].get("score")]
            out = {**out, "note": f"{len(same)} entities match this name equally (namesakes: {', '.join(map(str, same))}): "
                                  "use all of them (filter op in) and show each; do not refuse as ambiguous"}
        if name == "resolve" and state is not None:
            text = str(args.get("text", ""))
            values = {str(c.get("id")) for c in out.get("candidates", ())} | {str(c.get("name"))
                                                                             for c in out.get("candidates", ())}
            if out.get("matched_spellings"):
                values.add(text.strip())
            state.setdefault("resolved", []).append(Resolution(str(args.get("kind", "")), text, frozenset(values)))
        return out

    def _other_kind(self, kind: str, text: str) -> str:
        """A note naming the other kinds (company / broker / product) the text matches well, or ''."""
        found = []
        for k in _CROSS_KINDS:
            if k == kind:
                continue
            try:
                cands = [c for c in self.resolver.resolve(k, text) if c.score >= _CROSS_MIN_SCORE]
            except Exception:
                continue
            if cands:
                found.append(f"{k} ({', '.join(c.name for c in cands[:3])})")
        if not found:
            return ""
        return (f"no {kind} matches «{text}»; it matches " + "; ".join(found) +
                ": call resolve with that kind and use what it returns")

    def _resolve_catalog(self, text: str) -> dict:
        """Catalog / status values matching the text (inflection-tolerant), with the plan fields that take them.
        A field or catalog name (category, activity, status, ...) lists all its values."""
        key = text.strip().lower()
        fields_of = {}
        for s in self.dictionary.subjects.values():
            for f in s.fields.values():
                cat = f.catalog or (f.name if f.ground == "enum" else "")
                if cat:
                    fields_of.setdefault(cat, set()).add(f.name)
        cat_of = {n: c for c, names in fields_of.items() for n in names}
        if key in cat_of or key in fields_of:
            cat = cat_of.get(key, key)
            values = sorted(self.catalog.get(cat, ())) or sorted({v for s in self.dictionary.subjects.values()
                                                                  for f in s.fields.values()
                                                                  if f.name == key for v in f.values})
            return {"catalog": cat, "fields": sorted(fields_of.get(cat, ())), "values": values[:60]}
        toks = normalize_text(text).split()
        scored = {}
        for v in self._guard_index:
            hit = sum(1 for g in v.tokens if any(_tok_match(g, w, False) for w in toks))
            if hit:
                score = hit / len(v.tokens)
                if score > scored.get((v.field, v.value), 0):
                    scored[(v.field, v.value)] = score
        best = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        if not best:
            return {"candidates": [], "note": "no catalog value matches; resolve(kind=catalog, text=<field name>) "
                                              "lists the values of a field"}
        return {"candidates": [{"catalog": c, "value": v, "fields": sorted(fields_of.get(c, {c}))}
                               for (c, v), _ in best]}

    def _resolve_variant(self, text, allowed, names) -> dict:
        out = super()._resolve_variant(text, allowed, names)
        if "note" in out:
            out["note"] = out["note"].replace("filters.product_variant_query",
                                              "a filter {field: product_variant, op: =, value: <this text>}")
            out["note"] = out["note"].replace("the compiler", "the builder")
        return out
