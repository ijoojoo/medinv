<script setup lang="ts">
import { appearance, themes } from "./appearance";
import HomeViewPicker from "./HomeViewPicker.vue";
</script>
<template>
  <section class="panel appearance-settings" aria-label="界面与操作设置">
    <h2>界面与操作</h2>
    <p class="form-note">
      按您的使用习惯设置，立即生效并保存在当前浏览器。切换布局会保留已打开的工作页签。
    </p>
    <h3>工作台方案</h3>
    <p class="form-note">
      工作台也可直接切换。此偏好按当前企业、账号与经营主体保存在本浏览器，仅显示您有权使用的方案。
    </p>
    <HomeViewPicker cards />
    <h3>导航布局</h3>
    <div class="layout-choices">
      <button
        v-for="[key, name, note] in [
          ['horizontal', '横向工作区', '顶部集中导航，为单据保留完整宽度'],
          ['sidebar', '侧栏工作区', '左侧分类导航，适合同时处理多个模块'],
        ]"
        :key="key"
        type="button"
        :aria-pressed="appearance.layout === key"
        @click="appearance.layout = key as 'horizontal' | 'sidebar'"
      >
        <span class="layout-preview" :class="key"><i></i><i></i><i></i></span
        ><strong>{{ name }}</strong
        ><small>{{ note }}</small>
      </button>
    </div>
    <h3>主题配色</h3>
    <div class="theme-choices">
      <button
        v-for="t in themes"
        :key="t.value"
        :aria-pressed="appearance.theme === t.value"
        @click="appearance.theme = t.value"
      >
        <span :class="'swatch-' + t.value"></span>{{ t.label }}
      </button>
    </div>
    <h3>单据明细密度</h3>
    <div class="theme-choices">
      <button
        :aria-pressed="appearance.density === 'compact'"
        @click="appearance.density = 'compact'"
      >
        紧凑 · 连续录入</button
      ><button
        :aria-pressed="appearance.density === 'comfortable'"
        @click="appearance.density = 'comfortable'"
      >
        舒适 · 更大行距
      </button>
    </div>
  </section>
</template>
<style scoped>
.appearance-settings {
  max-width: 1000px;
  margin: auto;
}
h2 {
  margin: 0;
}
h3 {
  font-size: 16px;
  margin-top: 28px;
}
.layout-choices {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.layout-choices button {
  text-align: left;
  padding: 18px;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: white;
  color: var(--ui-text);
}
.layout-choices strong,
.layout-choices small {
  display: block;
  margin-top: 12px;
}
small {
  color: var(--ui-muted);
  font-size: 13px;
}
.layout-preview {
  display: grid;
  height: 112px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  overflow: hidden;
  background: var(--ui-bg);
  grid-template-columns: 1fr 2fr;
  grid-template-rows: 22px 1fr;
  gap: 8px;
}
.layout-preview i {
  background: white;
  border-radius: 3px;
}
.layout-preview i:first-child {
  grid-column: span 2;
  background: var(--ui-accent);
}
.layout-preview.sidebar {
  grid-template-columns: 38px 1fr;
  grid-template-rows: 22px 1fr;
}
.layout-preview.sidebar i:first-child {
  grid-column: auto;
  grid-row: span 2;
}
.theme-choices {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.theme-choices button {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--ui-border);
  background: white;
  padding: 10px 16px;
  border-radius: 6px;
  color: var(--ui-text);
  font: inherit;
}
button[aria-pressed="true"] {
  border-color: var(--ui-accent);
  box-shadow: 0 0 0 1px var(--ui-accent);
  background: var(--ui-tint);
}
.theme-choices span {
  width: 18px;
  height: 18px;
  border-radius: 50%;
}
.swatch-blue {
  background: #245fce;
}
.swatch-teal {
  background: #087968;
}
.swatch-violet {
  background: #7054b8;
}
@media (max-width: 600px) {
  .layout-choices {
    grid-template-columns: 1fr;
  }
}
</style>
