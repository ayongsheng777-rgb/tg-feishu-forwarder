<template>
  <div class="aliases-page">
    <div class="bento-grid">
      <!-- 绑定列表 -->
      <div class="bento-card span-7 accent-spring">
        <div class="bento-title">
          <el-icon><Link /></el-icon> 群名称 ↔ 飞书群ID 绑定
          <el-button
            size="small"
            type="primary"
            style="margin-left: auto"
            :icon="Plus"
            @click="openDialog()"
          >
            新增绑定
          </el-button>
        </div>

        <el-table :data="aliases" v-loading="loading" empty-text="暂无绑定">
          <el-table-column prop="name" label="群名称" min-width="130">
            <template #default="{ row }">
              <span style="font-weight: 600">{{ row.name }}</span>
              <el-tag size="small" :type="row.auto_discovered ? 'info' : 'primary'" style="margin-left: 6px">
                {{ row.auto_discovered ? '自动' : '手动' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="feishu_chat_id" label="飞书群ID" min-width="240">
            <template #default="{ row }">
              <code class="chat-id">{{ row.feishu_chat_id }}</code>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-switch
                :model-value="row.enabled"
                @change="(v) => toggleEnabled(row, v)"
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="260" align="center">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="viewChannels(row)">频道</el-button>
              <el-button size="small" text type="success" @click="openClone(row)">克隆</el-button>
              <el-button size="small" text type="primary" @click="openDialog(row)">编辑</el-button>
              <el-button size="small" text type="danger" @click="removeAlias(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 使用说明 -->
      <div class="bento-card span-5 accent-citrus">
        <div class="bento-title">
          <el-icon><Guide /></el-icon> 如何使用
        </div>
        <div class="guide">
          <div class="guide-item">
            <div class="guide-badge" style="background: rgba(143,201,127,.25); color: #2d6a4f">1</div>
            <div>
              <b>自动识别</b>
              <p>机器人在群里收到任何命令时，会自动读取群名并登记绑定（标记为「自动」）。</p>
            </div>
          </div>
          <div class="guide-item">
            <div class="guide-badge" style="background: rgba(242,193,78,.3); color: #9a6a10">2</div>
            <div>
              <b>手动绑定</b>
              <p>在飞书群发送 <code>tg绑定 科技 oc_xxx...</code>，或点击左上角「新增绑定」。</p>
            </div>
          </div>
          <div class="guide-item">
            <div class="guide-badge" style="background: rgba(244,155,171,.25); color: #b04a5e">3</div>
            <div>
              <b>按群采集</b>
              <p>发送 <code>tg采集 科技</code>，即可采集所有转发目标为该群的 TG 频道。</p>
            </div>
          </div>
          <div class="guide-item">
            <div class="guide-badge" style="background: rgba(108,180,214,.25); color: #2f6d8c">4</div>
            <div>
              <b>状态报告</b>
              <p>发送 <code>tg状态</code> 获取运行报告；<code>tg状态 科技</code> 可发到指定群。</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新增/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑绑定' : '新增绑定'"
      width="440px"
      :close-on-click-modal="false"
    >
      <el-form label-width="90px">
        <el-form-item label="群名称" required>
          <el-input v-model="form.name" placeholder="如：科技、IT穿梭机" />
        </el-form-item>
        <el-form-item label="飞书群ID" required>
          <el-input v-model="form.feishu_chat_id" placeholder="oc_xxxxxxxxxxxxxxxx" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 绑定频道查看 -->
    <el-dialog v-model="channelsVisible" :title="`「${currentAlias?.name}」绑定的频道`" width="520px">
      <el-table :data="aliasChannels" v-loading="channelsLoading" empty-text="该群暂无绑定频道">
        <el-table-column prop="channel_title" label="频道名" min-width="180" />
        <el-table-column prop="channel_id" label="频道ID" min-width="120" />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-alert v-if="!channelsLoading && aliasChannels.length === 0" type="info" :closable="false" show-icon style="margin-top: 12px">
        前往「频道管理」，把频道的目标飞书群ID设为 {{ currentAlias?.feishu_chat_id }} 即可关联。
      </el-alert>
    </el-dialog>

    <!-- 一键克隆对话框 -->
    <el-dialog v-model="cloneVisible" title="一键克隆频道" width="480px" :close-on-click-modal="false">
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
        将「{{ cloneSource?.name }}」的所有 TG 频道复制到目标群。
      </el-alert>
      <el-form label-width="90px">
        <el-form-item label="源群">
          <el-tag>{{ cloneSource?.name }}</el-tag>
          <code class="chat-id" style="margin-left: 8px">{{ cloneSource?.feishu_chat_id }}</code>
        </el-form-item>
        <el-form-item label="目标群" required>
          <el-select v-model="cloneTargetId" placeholder="选择目标群" style="width: 100%">
            <el-option
              v-for="a in aliases.filter(x => x.id !== cloneSource?.id)"
              :key="a.id"
              :label="`${a.name} (${a.feishu_chat_id.slice(-12)})`"
              :value="a.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cloneVisible = false">取消</el-button>
        <el-button type="primary" :loading="cloning" @click="doClone" :disabled="!cloneTargetId">
          开始克隆
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { aliasApi } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'

const aliases = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref(null)
const form = ref({ name: '', feishu_chat_id: '' })

const channelsVisible = ref(false)
const channelsLoading = ref(false)
const aliasChannels = ref([])
const currentAlias = ref(null)

// --- 一键克隆 ---
const cloneVisible = ref(false)
const cloneSource = ref(null)
const cloneTargetId = ref(null)
const cloning = ref(false)

async function load() {
  loading.value = true
  try {
    const res = await aliasApi.list()
    aliases.value = res.data
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

function openDialog(row) {
  editing.value = row || null
  form.value = row
    ? { name: row.name, feishu_chat_id: row.feishu_chat_id }
    : { name: '', feishu_chat_id: '' }
  dialogVisible.value = true
}

async function save() {
  if (!form.value.name.trim() || !form.value.feishu_chat_id.trim()) {
    ElMessage.warning('名称和群ID不能为空')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await aliasApi.update(editing.value.id, form.value)
      ElMessage.success('已更新')
    } else {
      const res = await aliasApi.create(form.value)
      if (res.data.warning && res.data.warning.startsWith('⚠️')) {
        ElMessage.warning(res.data.warning)
      } else {
        ElMessage.success(res.data.warning || '已绑定')
      }
    }
    dialogVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, val) {
  try {
    await aliasApi.update(row.id, { enabled: val })
    row.enabled = val
  } catch {
    ElMessage.error('操作失败')
  }
}

async function removeAlias(row) {
  try {
    await ElMessageBox.confirm(`确定删除绑定「${row.name}」吗？`, '删除绑定', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await aliasApi.delete(row.id)
    ElMessage.success('已删除')
    load()
  } catch {
    ElMessage.error('删除失败')
  }
}

async function viewChannels(row) {
  currentAlias.value = row
  channelsVisible.value = true
  channelsLoading.value = true
  try {
    const res = await aliasApi.channels(row.id)
    aliasChannels.value = res.data
  } catch {
    ElMessage.error('查询失败')
  } finally {
    channelsLoading.value = false
  }
}

function openClone(row) {
  cloneSource.value = row
  cloneTargetId.value = null
  cloneVisible.value = true
}

async function doClone() {
  if (!cloneTargetId.value || !cloneSource.value) return
  cloning.value = true
  try {
    const res = await aliasApi.clone(cloneSource.value.id, cloneTargetId.value)
    const d = res.data
    ElMessage.success(
      `克隆完成：${d.source} → ${d.target}，新增 ${d.cloned} 个频道` +
      (d.skipped > 0 ? `，跳过 ${d.skipped} 个（已存在）` : '')
    )
    cloneVisible.value = false
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '克隆失败')
  } finally {
    cloning.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.chat-id {
  font-size: 12px;
  background: rgba(108, 180, 214, 0.12);
  padding: 3px 8px;
  border-radius: 6px;
  color: #2f6d8c;
}
.guide {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.guide-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.guide-badge {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
  flex-shrink: 0;
}
.guide-item p {
  margin: 4px 0 0;
  font-size: 13px;
  color: #6b7a88;
  line-height: 1.6;
}
.guide-item code {
  background: rgba(155, 126, 217, 0.12);
  color: #6d4fa8;
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 12px;
}
</style>
