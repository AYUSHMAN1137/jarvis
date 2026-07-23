"""Process-wide guard for the harmless comtypes COM-release crash.

Background
----------
``comtypes`` is pulled in by two libraries this app uses on Windows:

* ``pycaw``     -> master-volume control (audio endpoint COM objects)
* ``pywinauto`` -> UI Automation tree for Phase 5 (UIA COM objects)

Comtypes COM interface pointers are released from ``_compointer_base.__del__``
during Python garbage collection. By default comtypes initializes COM as an STA
(apartment-threaded), so when the GC happens to run on a DIFFERENT thread than
the one that created the object, ``Release()`` has to marshal across apartments
and can fault with::

    Exception ignored in: <function _compointer_base.__del__ ...>
      ... self.Release() ...
    OSError: exception: access violation writing 0x...

This is *non-fatal*: the interpreter already catches the exception (that's what
"Exception ignored in" means), reclaims the object's memory, and keeps running.
The only damage is console spam. We cannot restructure COM objects that are
created deep inside the third-party libraries, so we install a tiny guard that
swallows ONLY this specific GC-time cleanup error. Behaviour is otherwise
identical -- the object is freed exactly as before, minus the noisy traceback.

This must be installed once, early, before any COM object is created.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("J.A.R.V.I.S")

_INSTALLED = False


def install_com_release_guard() -> bool:
    """Wrap ``comtypes._compointer_base.__del__`` to swallow the harmless
    cross-apartment Release access violation. Idempotent and fail-soft (a no-op
    when comtypes isn't installed, e.g. on Linux/dev).
    """
    global _INSTALLED
    if _INSTALLED:
        return True

    # Locate the class. comtypes 1.4+ moved it to _post_coinit.unknwn; older
    # versions expose it directly on the comtypes package.
    cls = None
    try:
        from comtypes._post_coinit.unknwn import _compointer_base as cls  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            from comtypes import _compointer_base as cls  # type: ignore
        except Exception:  # noqa: BLE001 - comtypes absent / not Windows
            return False

    if getattr(cls, "_jarvis_release_guarded", False):
        _INSTALLED = True
        return True

    orig_del = getattr(cls, "__del__", None)
    if orig_del is None:
        # Nothing to guard; mark installed so we don't retry endlessly.
        _INSTALLED = True
        return True

    def _guarded_del(self):  # noqa: ANN001
        try:
            orig_del(self)
        except OSError:
            # Harmless cross-apartment COM Release during GC. The object's
            # memory is still reclaimed; only the traceback was noise.
            pass
        except Exception:  # noqa: BLE001 - never let cleanup crash the process
            pass

    try:
        cls.__del__ = _guarded_del  # type: ignore[assignment]
        cls._jarvis_release_guarded = True  # type: ignore[attr-defined]
        _INSTALLED = True
        logger.info("[STARTUP] comtypes COM-release guard installed (silences harmless GC noise).")
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[STARTUP] Could not install COM-release guard: %s", e)
        return False
