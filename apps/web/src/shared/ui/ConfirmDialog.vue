<script setup lang="ts">
import { nextTick, onUnmounted, ref } from 'vue'
const dialog = ref<HTMLDialogElement|null>(null), message = ref('')
let resolve: ((value:boolean)=>void)|undefined, trigger: HTMLElement|null = null
async function ask(text:string):Promise<boolean>{
  if(resolve)return false
  message.value=text;trigger=document.activeElement as HTMLElement|null
  const answer=new Promise<boolean>(done=>{resolve=done})
  await nextTick();dialog.value?.showModal()
  return answer
}
function finish(value:boolean){const done=resolve;resolve=undefined;dialog.value?.close();trigger?.focus();done?.(value)}
onUnmounted(()=>{resolve?.(false);resolve=undefined})
defineExpose({ask})
</script>
<template>
  <dialog ref="dialog" class="plan-confirm" aria-labelledby="confirmation-title" aria-describedby="confirmation-message" @cancel.prevent="finish(false)">
    <h2 id="confirmation-title">确认这个决定</h2><p id="confirmation-message">{{message}}</p>
    <div class="modal-actions"><button autofocus @click="finish(false)">取消</button><button class="primary" @click="finish(true)">确认继续</button></div>
  </dialog>
</template>
<style scoped>
.plan-confirm{width:min(480px,calc(100vw - 32px));margin:auto;padding:26px;border:1px solid #d6e3df;border-radius:14px;color:#243936;box-shadow:0 18px 80px #142f3e38}
.plan-confirm::backdrop{background:#19373255;backdrop-filter:blur(3px)}
.plan-confirm h2{font-size:20px;margin:0 0 16px}.plan-confirm p{line-height:1.8;font-size:14px;margin-bottom:26px}
</style>
