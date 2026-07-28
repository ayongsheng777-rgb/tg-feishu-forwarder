<template>
  <div class="channels-page">
    <el-card shadow="hover">
      <template #header>
        <div class="card-header">
          <span>频道管理</span>
          <div>
            <el-button type="primary" @click="showDialogs = true">
              <el-icon><Search /></el-icon> 从TG导入
            </el-button>
            <el-button type="success" @click="openAdd">
              <el-icon><Plus /></el-icon> 手动添加
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="channels" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="channel_id" label="TG频道ID" width="160" />
        <el-table-column prop="channel_title" label="频道名称" />
        <el-table-column prop="channel_username" label="用户名" width="150">
          <template #default="{ row }">
            {{ row.channel_username ? '@' + row.channel_username : '—' }}
          </template>
        </el-table-column>
        <el-table-column prop="feishu_chat_id" label="飞书群ID" width="200" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggleEnabled(row)" />
          </template>
        </el-table-column>
        <el-table-column prop="last_forwarded_id" label="最后转发ID" width="120" />
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- Add/Edit Dialog -->
    <el-dialog v-model="showForm" :title="editing ? '编辑频道' : '添加频道'" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="TG频道ID" v-if="!editing">
          <el-input v-model="form.channel_id" placeholder="-1001234567890" />
        </el-form-item>
        <el-form-item label="频道名称">
          <el-input v-model="form.channel_title" placeholder="频道显示名称" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="form.channel_username" placeholder="频道@用户名(可选)" />
        </el-form-item>
        <el-form-item label="飞书群ID">
          <el-input v-model="form.feishu_chat_id" placeholder="oc_xxxxxxx (飞书群聊ID)" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showForm = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- TG Dialogs Import -->
    <el-dialog v-model="showDialogs" title="从 Telegram 导入频道" width="700px">
      <el-input v-model="dialogSearch" placeholder="搜索频道名称..." clearable style="margin-bottom: 15px" />
      <el-table :data="filteredDialogs" v-loading="loadingDialogs" max-height="400" stripe>
        <el-table-column prop="title" label="频道名称" />
        <el-table-column prop="id" label="ID" width="180" />
        <el-table-column prop="username" label="用户名" width="150">
          <template #default="{ row }">
            {{ row.username ? '@' + row.username : '—' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="importChannel(row)">导入</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { channelApi } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const channels = ref([])
const loading = ref(false)
const showForm = ref(false)
const editing = ref(null)
const form = ref({ channel_id: '', channel_title: '', channel_username: '', feishu_chat_id: '', enabled: true })

const showDialogs = ref(false)
const dialogs = ref([])
const loadingDialogs = ref(false)
const dialogSearch = ref('')

const filteredDialogs = computed(() => {
  if (!dialogSearch.value) return dialogs.value
  const q = dialogSearch.value.toLowerCase()
  return dialogs.value.filter(d => d.title?.toLowerCase().includes(q))
})

async function loadChannels() {
  loading.value = true
  try {
    const res = await channelApi.list()
    channels.value = res.data
  } catch {
    ElMessage.error('加载频道列表失败')
  } finally {
    loading.value = false
  }
}

function openAdd() {
  editing.value = null
  form.value = { channel_id: '', channel_title: '', channel_username: '', feishu_chat_id: '', enabled: true }
  showForm.value = true
}

function openEdit(row) {
  editing.value = row
  form.value = { ...row }
  showForm.value = true
}

async function save() {
  if (!form.value.feishu_chat_id) {
    ElMessage.warning('请填写飞书群ID')
    return
  }
  try {
    if (editing.value) {
      await channelApi.update(editing.value.id, form.value)
    } else {
      await channelApi.create(form.value)
    }
    ElMessage.success('保存成功')
    showForm.value = false
    loadChannels()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}

async function toggleEnabled(row) {
  try {
    await channelApi.update(row.id, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用' : '已禁用')
  } catch {
    row.enabled = !row.enabled
    ElMessage.error('操作失败')
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除频道 "${row.channel_title || row.channel_id}"？`, '提示', { type: 'warning' })
    await channelApi.delete(row.id)
    ElMessage.success('已删除')
    loadChannels()
  } catch {
    // cancelled
  }
}

async function loadDialogs() {
  loadingDialogs.value = true
  try {
    const res = await channelApi.dialogs()
    dialogs.value = res.data.dialogs || []
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载频道列表失败，请先完成TG登录')
  } finally {
    loadingDialogs.value = false
  }
}

async function importChannel(dialog) {
  // Check if already exists
  const exists = channels.value.find(c => c.channel_id === dialog.id)
  if (exists) {
    ElMessage.warning('该频道已存在')
    return
  }
  form.value = {
    channel_id: dialog.id,
    channel_title: dialog.title,
    channel_username: dialog.username || '',
    feishu_chat_id: '',
    enabled: true,
  }
  editing.value = null
  showDialogs.value = false
  showForm.value = true
}

onMounted(() => {
  loadChannels()
})

// Watch for dialog opening
import { watch } from 'vue'
watch(showDialogs, (val) => {
  if (val) loadDialogs()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
