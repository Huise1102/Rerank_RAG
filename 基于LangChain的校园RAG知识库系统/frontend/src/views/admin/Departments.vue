<template>
  <div class="page">
    <div class="page-header">
      <div class="muted">
        部门用于知识库归属与问答可见范围，停用后该部门用户不再获得新问答权限
      </div>
      <el-button type="primary" @click="openCreate">新建部门</el-button>
    </div>

    <el-table :data="items" size="small" border>
      <el-table-column prop="name" label="部门名称" min-width="180" />
      <el-table-column prop="code" label="编码" width="120" />
      <el-table-column label="上级部门" width="160">
        <template #default="{ row }">{{ parentName(row.parent_id) }}</template>
      </el-table-column>
      <el-table-column prop="contact" label="联系人" width="150" />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
            {{ row.status === 'active' ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="170">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button
            link
            :type="row.status === 'active' ? 'danger' : 'success'"
            size="small"
            @click="toggleStatus(row)"
          >
            {{ row.status === 'active' ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑部门' : '新建部门'" width="480px">
      <el-form :model="form" label-position="top">
        <el-form-item label="部门名称" required>
          <el-input v-model="form.name" placeholder="如 教务处" />
        </el-form-item>
        <el-form-item label="部门编码" :required="!editingId">
          <el-input v-model="form.code" :disabled="Boolean(editingId)" placeholder="如 JWC，创建后不可修改" />
        </el-form-item>
        <el-form-item label="上级部门">
          <el-select v-model="form.parent_id" clearable placeholder="无" style="width: 100%">
            <el-option
              v-for="item in parentOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact" placeholder="可选，部门对接人" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" :max="9999" />
          <span class="muted" style="margin-left: 12px">数值越小越靠前</span>
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="active">启用</el-radio>
            <el-radio value="disabled">停用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createDepartment, listDepartments, updateDepartment } from '../../api/admin'

const items = ref([])
const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)

const form = reactive({
  name: '',
  code: '',
  parent_id: null,
  contact: '',
  sort_order: 0,
  status: 'active'
})

const parentOptions = computed(() => {
  if (!editingId.value) return items.value
  const blocked = new Set([editingId.value])
  let changed = true
  while (changed) {
    changed = false
    for (const item of items.value) {
      if (item.parent_id && blocked.has(item.parent_id) && !blocked.has(item.id)) {
        blocked.add(item.id)
        changed = true
      }
    }
  }
  return items.value.filter((item) => !blocked.has(item.id))
})

onMounted(load)

async function load() {
  items.value = await listDepartments()
}

function parentName(parentId) {
  if (!parentId) return '无'
  return items.value.find((item) => item.id === parentId)?.name || '-'
}

function formatTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function resetForm() {
  form.name = ''
  form.code = ''
  form.parent_id = null
  form.contact = ''
  form.sort_order = 0
  form.status = 'active'
}

function openCreate() {
  editingId.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.name = row.name
  form.code = row.code
  form.parent_id = row.parent_id || null
  form.contact = row.contact || ''
  form.sort_order = row.sort_order
  form.status = row.status
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入部门名称')
    return
  }
  if (!editingId.value && !form.code.trim()) {
    ElMessage.warning('请输入部门编码')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      parent_id: form.parent_id || null,
      contact: form.contact.trim() || null,
      sort_order: form.sort_order,
      status: form.status
    }
    if (editingId.value) {
      await updateDepartment(editingId.value, payload)
    } else {
      await createDepartment({ ...payload, code: form.code.trim() })
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleStatus(row) {
  const nextStatus = row.status === 'active' ? 'disabled' : 'active'
  if (nextStatus === 'disabled') {
    try {
      await ElMessageBox.confirm(
        `停用后「${row.name}」的用户将失去本部门知识库的问答权限，确认停用？`,
        '停用部门',
        { type: 'warning' }
      )
    } catch {
      return
    }
  }
  await updateDepartment(row.id, { status: nextStatus })
  ElMessage.success(nextStatus === 'active' ? '已启用' : '已停用')
  await load()
}
</script>
