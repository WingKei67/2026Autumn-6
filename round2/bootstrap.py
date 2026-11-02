import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'audit_review/code'))
# Reuse the reviewed frozen baseline and offline connection guard.
import audit_study as audit
from alpha_lab import runner as baseline_runner
from alpha_lab.data import recording_paths
os.environ.setdefault('OMP_NUM_THREADS','1')
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
