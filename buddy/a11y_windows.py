"""The Windows side of a11y.py: an app's real buttons, through Windows' own UI Automation.

The .NET version that PowerShell offers found only 2 unnamed elements in Notepad on a real Windows, as it can't see
inside modern apps. This uses the newer COM interface underneath it, which can.
"""
from comtypes import COMError

from .winsys import on_com_thread

CHILDREN, DESCENDANTS = 2, 4  # TreeScope
PROCESS_ID = 30002  # UIA_ProcessIdPropertyId
INVOKE_AVAILABLE, TOGGLE_AVAILABLE = 30031, 30041  # UIA_Is...PatternAvailablePropertyId
INVOKE, TOGGLE = 10000, 10015  # UIA_InvokePatternId, UIA_TogglePatternId
MAX_NODES = 4000


def _pressable(pid: int) -> tuple:
    """(the interface's module, [(name, role, element)]) for everything showing in the app that can be pressed."""
    import comtypes.client
    uia = comtypes.client.GetModule("UIAutomationCore.dll")
    automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
    windows = automation.GetRootElement().FindAll(CHILDREN, automation.CreatePropertyCondition(PROCESS_ID, pid))
    found = []
    for index in range(windows.Length):
        nodes = windows.GetElement(index).FindAll(DESCENDANTS, automation.CreateTrueCondition())
        for position in range(min(nodes.Length, MAX_NODES)):
            node = nodes.GetElement(position)
            try:
                name = (node.CurrentName or "").strip()
                if name and not node.CurrentIsOffscreen and (node.GetCurrentPropertyValue(INVOKE_AVAILABLE)
                                                             or node.GetCurrentPropertyValue(TOGGLE_AVAILABLE)):
                    found.append((name, node.CurrentLocalizedControlType, node))
            except COMError:  # elements vanish while the tree is being walked
                continue
    return uia, found


def names(pid: int) -> list[str]:
    return on_com_thread(lambda: [name for name, _, _ in _pressable(pid)[1]])


def press(pid: int, wanted: list[str]) -> str | None:
    """Presses the first of these names that's there, and says which. The names go in order of preference."""
    def work():
        uia, found = _pressable(pid)
        for name in wanted:
            for shown, _, node in found:
                if shown.lower() == name.lower():
                    if node.GetCurrentPropertyValue(INVOKE_AVAILABLE):
                        node.GetCurrentPattern(INVOKE).QueryInterface(uia.IUIAutomationInvokePattern).Invoke()
                    else:
                        node.GetCurrentPattern(TOGGLE).QueryInterface(uia.IUIAutomationTogglePattern).Toggle()
                    return shown
        return None
    return on_com_thread(work)
