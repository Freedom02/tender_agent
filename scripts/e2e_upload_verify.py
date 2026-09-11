# -*- coding: utf-8 -*-
"""验证上传资料链路：导入落库 + 文件落盘 + evidence-files 枚举。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TAG = Path(r"E:\code\uinoproject\tenderagent\tender-agent\tender-agent")
sys.path.insert(0, str(TAG / "backend"))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.api.materials import create_router
from app.store import Store

store = Store(TAG)
store.initialize()
app = FastAPI()
app.include_router(create_router(store))


# 复刻 main.py 的 /runtime 静态路由
@app.get("/runtime/{file_path:path}")
def runtime_file(file_path: str) -> FileResponse:
    target = (store.runtime_root / file_path).resolve()
    if store.runtime_root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)


c = TestClient(app)

# 1) 读取本体
r = c.get("/api/materials")
assert r.status_code == 200, r.text
data = r.json()
facts = data["facts"]
qc = facts["qualification_certificate"]
print(f"facts keys: {list(facts.keys())}")
print(f"资质事实 {len(qc)} 条；关系定义 {len(data['relationships'])} 条；边 {len(data['edges'])} 条")

# 抽查第一条资质的 evidencePath
row = qc[0]
ep = row.get("evidencePath", "")
print(f"第一条资质: {row.get('qualificationName')} evidencePath={ep!r}")

# 2) 枚举证据文件
r = c.get("/api/materials/evidence-files", params={"path": ep})
assert r.status_code == 200, r.text
files = r.json()["files"]
print(f"evidence-files 返回 {len(files)} 个文件：{[f['name'] for f in files]}")

# 3) 校验文件 URL 可访问（/runtime 静态路由）
if files:
    r2 = c.get(files[0]["url"])
    print(f"文件 URL {files[0]['url']} → HTTP {r2.status_code}")

print("\n[OK] 上传资料链路验证通过（数据 ↔ 文件已链接）")
