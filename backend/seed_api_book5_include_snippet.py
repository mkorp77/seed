"""Book 5 include snippet for seed_api.py.

Copy the imports near existing router imports and the include_router calls near
other Book routers. Do not add a prefix here; seed_api.py already owns /api.
"""

from seed_router import router as seed_model_router
from seed_compare import router as seed_compare_router
from seed_collab import router as seed_collab_router

router.include_router(seed_model_router)
router.include_router(seed_compare_router)
router.include_router(seed_collab_router)
