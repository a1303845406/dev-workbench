<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { get, post } from '../../shared/api/http'
const emit=defineEmits<{close:[];changed:[]}>()
type Config={mode:string;name:string;base_url:string;model:string;source:string;has_key:boolean;secure_storage:string;priority:string}
const saved=ref<Config|null>(null), mode=ref('deterministic'), name=ref(''), base=ref(''), model=ref(''), key=ref(''), action=ref('keep'), external=ref(false), busy=ref(''), error=ref(''), message=ref('')
const title=ref<HTMLElement|null>(null)
const modeLabel=(v:string)=>({real:'真实模型 API',codex:'本机 Codex',deterministic:'规则测试（非 AI）'}[v]??v)
function apply(c:Config){saved.value=c;mode.value=c.mode;name.value=c.name;base.value=c.base_url;model.value=c.model;key.value='';action.value='keep';external.value=false}
async function load(){try{apply(await get<Config>('/api/v2/settings/model'))}catch(e){error.value=e instanceof Error?e.message:String(e)}}
function payload(){return {mode:mode.value,name:name.value,base_url:base.value,model:model.value,key_action:action.value,api_key:action.value==='replace'?key.value:'',confirm_external:external.value}}
async function run(kind:'save'|'test'|'reset'){
 busy.value=kind;error.value='';message.value=''
 try{
  if(kind==='test'){const result=await post<{message:string;latency_ms?:number}>('/api/v2/settings/model/test',payload());message.value=result.message+(result.latency_ms!==undefined?' 耗时 '+result.latency_ms+' ms':'')}
  else {const c=await post<Config>('/api/v2/settings/model'+(kind==='reset'?'/reset':''),kind==='reset'?{}:payload());apply(c);message.value=kind==='save'?'模型设置已保存，后续请求立即生效。':'已移除保存设置，恢复环境变量/默认模式。';emit('changed')}
 }catch(e){error.value=e instanceof Error?e.message:String(e)}finally{busy.value=''}
}
function trap(e:KeyboardEvent){const nodes=Array.from((e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="-1"]'));const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}
function close(){if(!busy.value){key.value='';emit('close')}}
function keyboard(e:KeyboardEvent){if(e.key==='Escape'){e.stopImmediatePropagation();close()}}
onMounted(()=>{load();title.value?.focus();window.addEventListener('keydown',keyboard,true)})
onUnmounted(()=>{key.value='';window.removeEventListener('keydown',keyboard,true)})
</script>
<template>
<div class="model-overlay" @click.self="close"><section class="model-settings" role="dialog" aria-modal="true" aria-labelledby="model-title" @keydown.tab="trap">
<header><div><small>全局设置 · 所有项目共用</small><h2 id="model-title" ref="title" tabindex="-1">模型服务</h2></div><button type="button" aria-label="关闭模型设置" :disabled="!!busy" @click="close">×</button></header>
<div v-if="saved" class="model-current"><strong>当前生效：{{modeLabel(saved.mode)}}</strong><span>来源：{{saved.source==='saved'?'已保存设置':saved.source==='environment'?'环境变量':'默认设置'}} · {{saved.has_key?'密钥已配置（不回显）':'未配置 API 密钥'}}</span><small>{{saved.priority}}</small></div>
<div v-if="error" class="feedback error" role="alert">{{error}}</div><div v-if="message" class="feedback success" role="status">{{message}}</div>
<form @submit.prevent="run('save')"><fieldset :disabled="!!busy">
<label>运行模式<select v-model="mode"><option value="real">真实模型 API（OpenAI 兼容）</option><option value="codex">本机 Codex CLI</option><option value="deterministic">规则测试（不调用模型）</option></select></label>
<label>服务名称<input v-model="name" maxlength="100" placeholder="例如 DeepSeek" autocomplete="off"></label>
<template v-if="mode==='real'"><label>API 基础地址<input v-model="base" placeholder="https://api.deepseek.com" autocomplete="off" :required="mode==='real'"></label><small>程序追加 /chat/completions；不要填写完整接口路径。非 HTTPS 地址会明文传输密钥，仅建议用于可信本机服务。</small></template>
<label v-if="mode!=='deterministic'">模型名称<input v-model="model" placeholder="例如 deepseek-chat" maxlength="200" required autocomplete="off"></label>
<template v-if="mode==='real'||saved?.has_key"><label>密钥处理<select v-model="action"><option value="keep">保留当前密钥</option><option value="replace">替换密钥</option><option value="clear">清除保存的密钥</option></select></label><label v-if="action==='replace'">新 API 密钥<input v-model="key" type="password" autocomplete="new-password" maxlength="8192" required placeholder="输入后不会回显"></label><small>更换服务地址时建议同时替换密钥；保留会沿用当前密钥。清除后真实 API 模式必须先补充新密钥或切换模式。</small></template>
<p v-if="mode==='codex'" class="model-note">使用本机 Codex 的登录和服务配置；API 密钥字段不会配置 Codex。测试在临时目录以只读沙箱运行，不访问项目。</p>
<p v-if="mode==='deterministic'" class="model-note">仅使用固定规则，不能视为模型审核或 AI 任务拆分。</p>
<p class="model-note">Windows 使用当前用户 DPAPI 加密保存密钥；其他系统仅支持环境变量密钥，不写入明文。配置保存在工作台数据目录，不进入被管理项目。保存设置不会自动测试连接。</p>
<label v-if="mode!=='deterministic'" class="model-check"><input v-model="external" type="checkbox">允许连接测试向所选模型发送一条无项目内容的短消息（可能产生费用）；实际审核/拆分会发送相应任务和技能。</label>
<footer><button type="button" @click="run('reset')">恢复环境配置</button><button type="button" @click="run('test')" :disabled="mode!=='deterministic'&&!external">{{busy==='test'?'测试中…':'测试连接（不保存）'}}</button><button class="primary" type="submit">{{busy==='save'?'保存中…':'保存并生效'}}</button></footer>
</fieldset></form></section></div>
</template>
<style scoped>
.model-overlay{position:fixed;inset:0;background:#132e3659;z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px}.model-settings{background:#fff;border-radius:18px;width:min(660px,100%);max-height:92vh;overflow:auto;box-shadow:0 24px 80px #17373d33;padding:26px}.model-settings header{display:flex;justify-content:space-between;align-items:start;margin-bottom:18px}.model-settings h2{margin:5px 0}.model-settings small{color:#62757d;line-height:1.6}.model-current{display:grid;gap:7px;background:#eef8f6;border:1px solid #c9e4de;border-radius:10px;padding:14px;margin-bottom:16px}.model-current span{font-size:13px}.model-settings fieldset{border:0;padding:0;margin:0;display:grid;gap:12px;min-width:0}.model-settings label{display:grid;gap:6px;font-size:14px;font-weight:600}.model-settings input,.model-settings select{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #cddadb;border-radius:8px;background:white}.model-settings .model-check{display:flex;align-items:start;font-weight:400;line-height:1.6}.model-check input{width:auto;margin-top:5px}.model-note{font-size:13px;line-height:1.6;color:#62757d;margin:2px 0}.model-settings footer{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap;border-top:1px solid #e6eeee;padding-top:16px}.model-settings button{padding:9px 13px;border-radius:8px;border:1px solid #cddadb;cursor:pointer;background:#fff}.model-settings button.primary{background:#177e70;color:white;border-color:#177e70}.model-settings :disabled{cursor:wait;opacity:.65}.model-settings .feedback{padding:12px;margin-bottom:12px}@media(max-width:600px){.model-overlay{padding:8px}.model-settings{padding:18px}.model-settings footer button{flex:1}}
</style>
