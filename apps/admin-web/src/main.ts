import { createApp, h } from "vue";
import {
  ElConfigProvider,
  ElAlert,
  ElButton,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElLoading,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from "element-plus";
import "element-plus/dist/index.css";
import "./style.css";
import App from "./App.vue";
import "./appearance.css";
import "./refined.css";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { installDateSubmissionGuard } from "./dateControls";
installDateSubmissionGuard();
const app = createApp({
  render: () => h(ElConfigProvider, { locale: zhCn }, () => h(App)),
});
for (const component of [
  ElAlert,
  ElButton,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
]) {
  app.component(component.name!, component);
}
app.directive("loading", ElLoading.directive);
app.mount("#app");
