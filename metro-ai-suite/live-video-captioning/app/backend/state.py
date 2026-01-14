from .models import RunInfo

# In-memory storage for active runs
RUNS: dict[str, RunInfo] = {}
