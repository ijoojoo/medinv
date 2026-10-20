import { computed, reactive, watch, type Ref } from "vue";
import { ApiError, request, session } from "./api";
import {
  loadEntryColumns,
  normalizeEntryColumns,
  type EntryChannel,
  type EntryColumn,
} from "./documentEntry";

interface TemplateReply {
  version: string;
  template: { schema_version: 2; columns: EntryColumn[] } | null;
}
interface TemplateState {
  scope: string;
  channel: EntryChannel;
  columns: EntryColumn[];
  version: string;
  loading: boolean;
  ready: boolean;
  error: string;
  saving: boolean;
  pending?: { key: string; body: string };
}
const states = new Map<string, TemplateState>();
const scope = () =>
  `${session.value?.tenant_id}:${session.value?.principal_id}`;
watch(scope, () => states.clear());
export function useEntryTemplate(channel: Ref<EntryChannel>) {
  return computed(() => {
    const key = `${scope()}:${channel.value}`;
    let state = states.get(key);
    if (!state) {
      state = reactive({
        scope: scope(),
        channel: channel.value,
        columns: loadEntryColumns(`medinv.entry-columns.v1:${key}`),
        version: "0",
        loading: false,
        ready: false,
        error: "",
        saving: false,
      });
      states.set(key, state);
      void reloadEntryTemplate(state);
    }
    return state;
  });
}
export async function reloadEntryTemplate(state: TemplateState) {
  if (state.loading || state.saving || state.scope !== scope()) return;
  state.loading = true;
  state.error = "";
  try {
    const result = await request<TemplateReply>(
      `/api/v1/entry-templates/${state.channel}`,
    );
    if (state.scope !== scope()) return;
    if (result.template)
      state.columns = normalizeEntryColumns(result.template.columns);
    state.version = result.version;
    state.ready = true;
    state.pending = undefined;
  } catch (e) {
    state.error = e instanceof Error ? e.message : "字段设置读取失败";
  } finally {
    state.loading = false;
  }
}
export async function saveEntryTemplate(
  state: TemplateState,
  columns: EntryColumn[],
  version: string,
) {
  if (!state.ready || state.saving || state.scope !== scope()) return false;
  state.saving = true;
  state.error = "";
  // Preserve the exact request and key after a lost response, even if a dialog is closed.
  state.pending ??= {
    key: crypto.randomUUID(),
    body: JSON.stringify({
      expected_version: version,
      template: {
        schema_version: 2,
        columns: normalizeEntryColumns(columns).map(
          ({ required, editable, ...c }) => c,
        ),
      },
    }),
  };
  try {
    const result = await request<TemplateReply>(
      `/api/v1/entry-templates/${state.channel}`,
      {
        method: "POST",
        headers: { "Idempotency-Key": state.pending.key },
        body: state.pending.body,
      },
    );
    if (state.scope !== scope()) return false;
    state.columns = normalizeEntryColumns(result.template?.columns);
    state.version = result.version;
    state.pending = undefined;
    return true;
  } catch (e) {
    state.error = e instanceof Error ? e.message : "字段设置保存失败";
    if (e instanceof ApiError && e.status >= 400 && e.status < 500)
      state.pending = undefined;
    return false;
  } finally {
    state.saving = false;
  }
}
