"""Shared route helpers."""

import json
import numpy as np
from fastapi.responses import JSONResponse


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def json_response(data: dict) -> JSONResponse:
    """Return a JSONResponse that handles numpy types."""
    content = json.loads(json.dumps(data, cls=NumpyEncoder))
    return JSONResponse(content=content)
