import os
from pathlib import Path
from siamang.storage import Database
from siamang.projects import ProjectRegistry
SANDBOX='Office Sandbox'
root=Path(os.environ['WEB_DATA'])
with Database(root/'office.db') as db:
    registry=ProjectRegistry(db)
    existing=next((p for p in registry.list(status=None) if p.name==SANDBOX),None)
    if os.environ.get('WEB_SANDBOX')=='1':
        project=root/'sandbox'
        project.mkdir(parents=True,exist_ok=True)
        for name,content in {'backend/calculator.py':'def add(a, b):\n    pass\n','tests/test_calculator.py':'def test_add():\n    pass\n','backend/pyproject.toml':'[project]\nname = "office-sandbox"\nversion = "0.1.0"\n','README.md':'# Office sandbox\nA local proposal-only test project.\n'}.items():
            p=project/name;p.parent.mkdir(parents=True,exist_ok=True)
            if not p.exists():p.write_text(content)
        if existing is None:registry.register(name=SANDBOX,root_path=str(project))
        elif str(existing.status)=='archived':registry.restore(existing.project_id)
    elif existing is not None and str(existing.status)!='archived':registry.archive(existing.project_id)
print('Local registry ready')
