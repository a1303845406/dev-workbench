<script setup lang="ts">
/** Markdown 渲染（markdown-it + DOMPurify 消毒）。 */
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ source: string }>()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const html = computed(() => DOMPurify.sanitize(md.render(props.source || '')))
</script>

<template>
  <div class="md-body" v-html="html" />
</template>
