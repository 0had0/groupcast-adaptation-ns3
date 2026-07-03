"""common.kpi.kpis — KPI spec implementations (one file per KPI)."""

# Importing each module here triggers its @register decorator, populating
# the KpiRegistry. To add a new KPI, create a new file in this directory and
# import it below — no other code changes needed.
from . import prr_type1       # noqa: F401
from . import prr_type2       # noqa: F401
from . import pir_type1       # noqa: F401
from . import pir_type2       # noqa: F401
from . import throughput      # noqa: F401
from . import aoi             # noqa: F401
from . import harq            # noqa: F401
from . import ch_occupancy    # noqa: F401