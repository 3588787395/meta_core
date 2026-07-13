import sys
import os
from os.path import dirname

# Add vendor directory to sys.path for the .pyd import
_vendor_dir = dirname(dirname(__file__))
if _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

from HQChartPy2.HQChartPy2 import LoadAuthorizeInfo, Run, GetAuthorizeInfo, GetVersion, SetLog