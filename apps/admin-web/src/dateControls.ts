import type { DateResult } from "./dates";
type Control = {
  element: () => HTMLElement | undefined;
  confirm: () => DateResult;
  focus: () => void;
  commit: (value: string) => void;
  disabled: () => boolean;
};
const controls = new Set<Control>();
export function registerDateControl(control: Control) {
  controls.add(control);
  return () => controls.delete(control);
}
/** Two phases: read every current input, then commit the confirmed snapshot. Never reuse an earlier valid value. */
export function confirmDates(scope: ParentNode | null | undefined): boolean {
  if (!scope) return true;
  const snapshot = [...controls]
    .filter((c) => c.element() && scope.contains(c.element()!) && !c.disabled())
    .map((control) => ({ control, result: control.confirm() }));
  const invalid = snapshot.find((s) => s.result.error);
  if (invalid) {
    invalid.control.focus();
    return false;
  }
  for (const { control, result } of snapshot) control.commit(result.value);
  return true;
}
export function installDateSubmissionGuard() {
  // Capture before Vue's form handler, including requestSubmit and keyboard submits.
  document.addEventListener(
    "submit",
    (event) => {
      if (!confirmDates(event.target as HTMLFormElement)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    },
    true,
  );
}
