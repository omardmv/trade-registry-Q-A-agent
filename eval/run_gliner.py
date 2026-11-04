import json, sys, time
from gliner2 import GLiNER2
qs = json.load(open(sys.argv[1]))
m = GLiNER2.from_pretrained("fastino/gliner2-base-v1")
schema = m.create_schema().entities({
 "product": "commodity or product name being traded",
 "region": "region, province or city in Uzbekistan",
 "inn": "9-digit company tax ID (STIR/INN)",
 "broker": "broker code like BK123",
 "time": "year, month, quarter or date"})
m.extract(qs[0]["question"], schema)  # warm-up
out = {}
for q in qs:
    t = time.perf_counter()
    r = m.extract(q["question"], schema, include_confidence=True)
    out[q["id"]] = {"ms": (time.perf_counter()-t)*1000, "result": r}
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, default=str)
print("done")
