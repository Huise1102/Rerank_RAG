<template>
  <el-dialog :model-value="modelValue" title="修改密码" width="420px" @update:model-value="emit('update:modelValue', $event)">
    <el-form :model="form" label-position="top">
      <el-form-item label="原密码">
        <el-input v-model="form.oldPassword" type="password" show-password />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="form.newPassword" type="password" show-password />
      </el-form-item>
      <el-form-item label="确认新密码">
        <el-input v-model="form.confirm" type="password" show-password />
      </el-form-item>
      <div class="muted">密码至少 8 位，需同时包含字母和数字</div>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { changePassword } from '../api/auth'

defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'changed'])
const form = reactive({ oldPassword: '', newPassword: '', confirm: '' })
const loading = ref(false)

async function submit() {
  if (form.newPassword.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  if (form.newPassword !== form.confirm) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  loading.value = true
  try {
    await changePassword({ old_password: form.oldPassword, new_password: form.newPassword })
    ElMessage.success('密码已修改，其他登录会话已失效')
    form.oldPassword = ''
    form.newPassword = ''
    form.confirm = ''
    emit('update:modelValue', false)
    emit('changed')
  } finally {
    loading.value = false
  }
}
</script>
