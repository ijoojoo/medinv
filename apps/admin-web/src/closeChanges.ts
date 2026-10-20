import { shallowRef } from "vue";
export const closeRequest = shallowRef<{
  message: string;
  resolve: (choice: "save" | "discard" | "cancel") => void;
} | null>(null);
export function askCloseChanges(message: string) {
  if (closeRequest.value) return Promise.resolve("cancel" as const);
  return new Promise<"save" | "discard" | "cancel">((resolve) => {
    closeRequest.value = { message, resolve };
  });
}
export function resolveCloseChanges(choice: "save" | "discard" | "cancel") {
  const request = closeRequest.value;
  closeRequest.value = null;
  request?.resolve(choice);
}
