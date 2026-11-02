from pathlib import Path
import os
import sys
from network_guard import install
install()
(Path(__file__).resolve().parents[1]/'results').mkdir(parents=True,exist_ok=True)
root=Path(__file__).resolve().parents[1]/'extracted/2026Autumn-6-fresh'
sys.path.insert(0,str(root));os.chdir(root)
import pytest
raise SystemExit(pytest.main(['tests','-q','--junitxml='+str(Path(__file__).resolve().parents[1]/'results/archive_tests.xml')]))
