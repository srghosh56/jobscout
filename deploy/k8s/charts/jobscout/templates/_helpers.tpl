{{- define "jobscout.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "jobscout.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "jobscout.labels" -}}
{{ include "jobscout.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "jobscout.secretName" -}}
{{- default (printf "%s-secrets" (include "jobscout.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "jobscout.pgHost" -}}
{{ include "jobscout.fullname" . }}-postgres
{{- end -}}

{{- define "jobscout.env" -}}
- name: LLM_PROVIDER
  value: {{ .Values.app.llmProvider | quote }}
- name: CHAT_MODEL
  value: {{ .Values.app.chatModel | quote }}
- name: JUDGE_MODEL
  value: {{ .Values.app.judgeModel | quote }}
- name: EMBED_MODEL
  value: {{ .Values.app.embedModel | quote }}
- name: OLLAMA_BASE_URL
  value: {{ .Values.app.ollamaBaseUrl | quote }}
- name: RERANKER_ENABLED
  value: {{ .Values.app.rerankerEnabled | quote }}
- name: PUBLIC_BASE_URL
  value: {{ .Values.app.publicBaseUrl | quote }}
- name: OTEL_ENABLED
  value: {{ .Values.app.otelEnabled | quote }}
{{- if .Values.app.otelEndpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.app.otelEndpoint | quote }}
{{- end }}
- name: HOME
  value: /tmp
{{- range $k := list "DATABASE_URL" "READONLY_DATABASE_URL" "MISTRAL_API_KEY" "GEMINI_API_KEY" }}
- name: {{ $k }}
  valueFrom:
    secretKeyRef:
      name: {{ include "jobscout.secretName" $ }}
      key: {{ $k }}
      optional: true
{{- end }}
{{- end -}}

{{- define "jobscout.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "jobscout.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
