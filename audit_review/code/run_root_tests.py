"""Verify the existing repository suite without collecting extracted copies."""
from pathlib import Path
import sys,os
from network_guard import install
install()
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root));os.chdir(root)
import pytest
raise SystemExit(pytest.main(['-q','--junitxml='+str(root/'audit_review/results/root_tests.xml')]))
