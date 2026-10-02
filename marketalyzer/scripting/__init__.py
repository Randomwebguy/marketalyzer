"""A Pine Script-like language for indicators and strategies.

Scripts are written like TradingView Pine Script v5 and run on BIST bars.
Indicator scripts draw series; strategy scripts also become backtesting.py
strategies that can be backtested, optimized, walked forward and paper traded.
"""

from marketalyzer.scripting.builtins import reference
from marketalyzer.scripting.errors import ScriptError
from marketalyzer.scripting.runtime import (
    InputSpec,
    Script,
    ScriptResult,
    compile_script,
)
from marketalyzer.scripting.store import (
    delete_script,
    get_script,
    list_scripts,
    load_script,
    load_strategy,
    save_script,
    script_strategies,
)
from marketalyzer.scripting.strategy import (
    ScriptStrategy,
    default_grid,
    script_strategy,
)

__all__ = [
    "InputSpec",
    "Script",
    "ScriptError",
    "ScriptResult",
    "ScriptStrategy",
    "compile_script",
    "default_grid",
    "delete_script",
    "get_script",
    "list_scripts",
    "load_script",
    "load_strategy",
    "reference",
    "save_script",
    "script_strategies",
    "script_strategy",
]
