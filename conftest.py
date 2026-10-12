"""Settings every test runs under, whatever is in .env."""

import os

# A test run never exports traces to a real backend. Tests that need one build their own.
os.environ["DEFLECT_TRACE_BACKEND"] = "none"
