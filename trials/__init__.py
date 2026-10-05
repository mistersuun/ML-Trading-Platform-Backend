"""Trial registry (WS4.1): every evaluation of a run is recorded so N (trials) is honest for deflated Sharpe."""
from trials.registry import (TrialRecord, TrialRegistry, TrialRegistryError, data_hash, load_run,
                             matrix_hash, params_hash)

__all__ = ["TrialRecord", "TrialRegistry", "TrialRegistryError", "data_hash", "load_run", "matrix_hash",
           "params_hash"]
