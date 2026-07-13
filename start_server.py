import sys, os
base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
mc = os.path.dirname(__file__)
for p in (base, mc):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ["PYTHONPATH"] = base
os.chdir(mc)
import uvicorn
uvicorn.run("meta_core.app:app", host="127.0.0.1", port=18799, access_log=False)
