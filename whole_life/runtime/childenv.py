"""The one child-environment builder. Normative source: spec section 5.

Authentication status commands and provider processes must receive the *same*
newly built environment. Two builders, or one builder used in one place and
`os.environ` in the other, would let a status check pass against credentials
that the real run never sees.

The parent process environment is never modified.
"""

from collections.abc import Mapping
from types import MappingProxyType

from whole_life.runtime.launch import PreStartRefusal, RefusalCode

#: Removed regardless of whether the parent has them. The first four select a
#: credential path other than the operator's subscription sign-in.
#: ``CLAUDE_CODE_SIMPLE`` is not an API-key variable at all — it is a mode
#: switch that makes Claude Code behave as if ``--bare`` were passed, which
#: reads an API key and never OAuth. It belongs on this list for that reason.
FORBIDDEN_VARIABLES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "CLAUDE_CODE_SIMPLE",
    }
)

#: Carried over from the parent when present. Enough for the official CLIs to
#: locate themselves, their configuration and the operator's existing sign-in,
#: and nothing else. Verified sufficient against Claude Code 2.1.239 and
#: Codex CLI 0.149.0.
INHERITED_VARIABLES = (
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
)

assert not (FORBIDDEN_VARIABLES & set(INHERITED_VARIABLES))


def build_child_env(
    parent_env: Mapping[str, str],
    *,
    extra: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Build the sanitized child environment.

    ``extra`` is for values the broker sets deliberately, such as ``CODEX_HOME``,
    which is specified rather than inherited so that the authentication check
    and the real run cannot end up reading different credential stores.

    Refuses rather than filters when a forbidden variable is asked for: a caller
    trying to set one is a defect, not something to silently clean up.
    """
    env = {
        name: parent_env[name] for name in INHERITED_VARIABLES if name in parent_env
    }
    env.update(extra or {})

    if FORBIDDEN_VARIABLES & set(env):
        # The code is the whole diagnostic; naming the variable here would put
        # it one careless format string away from its value.
        raise PreStartRefusal(RefusalCode.CHILD_ENV_FORBIDDEN_VARIABLE)

    return MappingProxyType(env)
