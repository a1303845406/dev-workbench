# Planning workspace implementation contract

Base `/api/v2/projects/{pid}/planning`. GET returns State. Every POST returns State; optimistic locking `expected_revision` required. Generation runs in background; UI polls GET while active and provides cancel. All user/model data is plain text unless sandboxed prototype.

State: `{revision:number,provider_mode:string,messages:Message[],versions:Version[],current:{requirement:string|null,decomposition:string|null,design:string|null},runs:Run[],sources:Source[],stage:string,execution_locked:true}`.
Message: `{id,role:user|assistant,content,created_at,run_id?}`.
Version: `{id,kind:requirement|decomposition|design,number:number,data,origin:ai|human,status:proposed|draft|approved|changes_requested|stale|rejected,based_on:string|null,requirement_version_id:string|null,decomposition_version_id:string|null,created_at,checks:string[],review_comment?:string,reviews:[{decision,comment,created_at,version_id}]}`.
- requirement data: `{document:string,unresolved:string[],checks:string[]}`
- decomposition data: `{overview:string,nodes:Node[],checks:string[],unresolved:string[]}`
- Node: `{id,parent_id:string|null,type:module|feature,title,responsibility,exclusions,requirement_ids:string[],acceptance,dependencies:string[],rationale}`
- design data: `{detailed_design:string,prototype_html:string,data_design:string,checks:string[],unresolved:string[]}`
Run: `{id,action,status:running|completed|failed|cancelled|interrupted,error:string,created_at,provider_mode:string,evidence:Source[],tools:[{tool,status,message,finish_reason?:string,usage?:{prompt_tokens?,completion_tokens?,total_tokens?,prompt_cache_hit_tokens?,prompt_cache_miss_tokens?}}],response_text?:string,result_version_id?:string,citations?:string[]}`.
- `model_completion` records only allowlisted provider metadata, not estimated cost; absent usage means unknown, including failed calls.
- Failed parsing/validation may retain API-key-redacted `response_text` (up to 500,000 characters) as plain text for local diagnosis; it can still contain sensitive project content. Never render it as HTML or auto-create/adopt a version. Earlier runs cannot be retroactively reconstructed.
- Errors distinguish JSON location, safe schema fields/tree reasons, HTTP status, timeout/network and truncation. No automatic paid retry.
- Search uses explicit public queries, bounded HTTPS redirects only to Bing search hosts; evidence is search snippets, not fetched page text or guaranteed relevance.
Source: `{id,title,kind:project|experience|knowledge|web,content,version,url?}`.

POST endpoints:
- `/generate`: `{action:discuss|requirements|decomposition|design,message:string,expected_revision,allow_external:boolean,allow_search:boolean,search_query:string,target_node_id?:string}`. Returns immediately with running run; discuss can propose requirement version. Consent required; deterministic returns honest MODEL_REQUIRED, never demo AI.
- `/runs/{id}/cancel`: `{expected_revision}`.
- `/versions`: `{kind,data,expected_revision}` save human immutable current draft.
- `/versions/{vid}/adopt`: `{expected_revision}` apply AI candidate to current (check based_on + upstream lineage).
- `/versions/{vid}/reject`: `{expected_revision}` reject AI proposal.
- `/versions/{vid}/review`: `{expected_revision,decision:approve|changes_requested,comment:string}` approves current exact version or records rework.
- `/sources`: `{title,kind:project|experience|knowledge,content,expected_revision}` add local project source, no global promotion.

UI: PlanningWorkspace.vue props projectId:string; emits changed/settings. Import get/post from api.ts, async requests return quickly. Show requirements chat + versioned rolling document + evidence, candidate accept/reject + before/after comparison; decomposition recursive tree + node details + coverage + local re-split feedback; detailed design/prototype/data design tabs and unified human review. History inspect; refresh/resume/cancel. Do not overwrite unsaved edits during polling. Approval on exact version; blocking unresolved must be addressed. Stale upstream cannot advance. No coding task creation or execution unlocked even after design approval. Treat sources as untrusted, prototype iframe sandbox WITHOUT allow-same-origin/scripts/network/forms, use restrictive CSP. Supplemental editor not main workflow. Responsive light teal existing styles vars, no raw v-html. Sources library includes builtin knowledge plus project scoped added sources. Generation explicit consent model data, separately opt-in search using explicit public query.
