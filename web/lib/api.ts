// Typed client for the Guru FastAPI backend (backend/app). All calls go from the browser; the API
// allows this origin via CORS. Shapes mirror backend/app/jobs.py and sessions.py.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080';

export type QuestionBank = {
  text: string;
  category: string;
  difficulty: string;
  expected_signals: string[];
};

export type Criterion = {
  id: string;
  name: string;
  description: string;
  weight: number;
  anchors: Record<string, string>; // "1".."5"
};

export type Company = {
  id: string;
  name: string;
  roles: number;
  sources: number;
  updated_at: string;
};

export type JobInput = {
  title: string;
  jd_text: string;
  max_duration_sec: number;
  questions: QuestionBank[];
  criteria: Criterion[];
};

export type Job = JobInput & {
  id: string;
  company_id: string;
  company_name: string;
  version: number;
  updated_at: string;
};

export type JobSummary = {
  id: string;
  title: string;
  updated_at: string;
  questions: number;
  criteria: number;
};

export type SourceStatus =
  'uploading' | 'queued' | 'parsing' | 'parsed' | 'embedding' | 'ready' | 'failed' | 'duplicate';

export type Source = {
  id: string;
  kind: 'pdf' | 'docx' | 'txt' | 'md' | 'csv' | 'url';
  name: string;
  size_bytes: number | null;
  status: SourceStatus;
  error: string | null;
  chunks: number;
  created_at: string;
};

export type PlannedQuestion = {
  text: string;
  expected_signals: string[];
  follow_up_probes: string[];
  time_budget_sec: number;
  linked_criteria: string[];
};

export type InterviewPlan = {
  questions: PlannedQuestion[];
  skipped_questions: { text: string; reason: string }[];
  allowed_tech_stack: string[];
};

export type PlanState = {
  status: 'none' | 'generating' | 'ready' | 'failed';
  stale: boolean;
  error: string | null;
  plan: InterviewPlan | null;
  updated_at?: string;
};

export type SessionStatus = 'created' | 'active' | 'completed' | 'declined';
export type ScoreStatus = 'none' | 'scoring' | 'scored' | 'failed';

export type SessionSummary = {
  id: string;
  job_id: string;
  candidate_name: string;
  status: SessionStatus;
  invite_expires_at: string | null;
  expired: boolean;
  score_status: ScoreStatus;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  duration_sec: number | null;
  overall: number | null;
};

export type Turn = { idx: number; speaker: 'candidate' | 'interviewer'; text: string };

export type ObserverNote = {
  question_idx: number;
  note: {
    coverage: number;
    depth: 'shallow' | 'adequate' | 'deep';
    signals_hit: string[];
    signals_missing: string[];
    red_flags: string[];
    suggested_followup: string | null;
  };
};

export type CriterionScore = {
  criterion_id: string;
  score: number | null;
  insufficient_evidence: boolean;
  evidence_turns: number[];
  rationale: string;
};

export type SessionDetail = SessionSummary & {
  plan: InterviewPlan;
  criteria: Criterion[];
  transcript: Turn[];
  notes: ObserverNote[];
  usage: Record<string, unknown>;
  score: { overall: number | null; criteria: CriterionScore[]; advisory: string } | null;
  score_error: string | null;
};

export type InterviewInfo = {
  id: string;
  candidate_name: string;
  status: SessionStatus;
  job_title: string;
  company_name: string;
  max_duration_sec: number;
  expired: boolean;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers
        : { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    // FastAPI errors: {"detail": "..."} or a validation list
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail
          .map((d: { loc?: string[]; msg: string }) => `${d.loc?.slice(1).join('.')}: ${d.msg}`)
          .join('; ')
      : (detail ?? res.statusText);
    throw new ApiError(res.status, message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  listCompanies: () => request<Company[]>('/companies'),
  getCompany: (id: string) => request<Company>(`/companies/${id}`),
  createCompany: (name: string) => request<Company>('/companies', json('POST', { name })),
  renameCompany: (id: string, name: string) =>
    request<Company>(`/companies/${id}`, json('PUT', { name })),
  deleteCompany: (id: string) => request<void>(`/companies/${id}`, json('DELETE')),

  listJobs: (companyId: string) => request<JobSummary[]>(`/companies/${companyId}/jobs`),
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  createJob: (companyId: string, job: JobInput) =>
    request<Job>(`/companies/${companyId}/jobs`, json('POST', job)),
  updateJob: (id: string, job: JobInput) => request<Job>(`/jobs/${id}`, json('PUT', job)),
  deleteJob: (id: string) => request<void>(`/jobs/${id}`, json('DELETE')),
  importQuestions: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<QuestionBank[]>('/questions/import', { method: 'POST', body: form });
  },

  listSources: (companyId: string) => request<Source[]>(`/companies/${companyId}/sources`),
  addUrl: (companyId: string, url: string) =>
    request<Source>(`/companies/${companyId}/sources/url`, json('POST', { url })),
  retrySource: (id: string) => request<Source>(`/sources/${id}/retry`, json('POST')),
  deleteSource: (id: string) => request<void>(`/sources/${id}`, json('DELETE')),
  /** Presigned upload: ask the API for a URL, PUT the file straight to storage, then confirm. */
  uploadFile: async (companyId: string, file: File) => {
    const contentType = file.type || 'application/octet-stream';
    const { source, upload_url, headers } = await request<{
      source: Source;
      upload_url: string;
      headers: Record<string, string>;
    }>(
      `/companies/${companyId}/sources/upload`,
      json('POST', { filename: file.name, content_type: contentType, size_bytes: file.size })
    );
    const put = await fetch(upload_url, { method: 'PUT', body: file, headers });
    if (!put.ok) {
      await request<void>(`/sources/${source.id}`, json('DELETE')).catch(() => {});
      throw new ApiError(put.status, `Upload to storage failed (HTTP ${put.status}).`);
    }
    return request<Source>(`/sources/${source.id}/uploaded`, json('POST'));
  },

  getPlan: (jobId: string) => request<PlanState>(`/jobs/${jobId}/plan`),
  generatePlan: (jobId: string) => request<PlanState>(`/jobs/${jobId}/plan`, json('POST')),

  listSessions: (jobId: string) => request<SessionSummary[]>(`/jobs/${jobId}/sessions`),
  createSession: (jobId: string, candidateName: string) =>
    request<SessionSummary>(
      `/jobs/${jobId}/sessions`,
      json('POST', { candidate_name: candidateName })
    ),
  renewLink: (id: string) => request<SessionSummary>(`/sessions/${id}/renew`, json('POST')),
  inviteDefaults: () => request<{ ttl_min: number }>('/invite-defaults'),
  getSession: (id: string) => request<SessionDetail>(`/sessions/${id}`),
  rescore: (id: string) => request<SessionSummary>(`/sessions/${id}/score`, json('POST')),

  interviewInfo: (id: string) => request<InterviewInfo>(`/interviews/${id}`),
  interviewToken: (id: string) =>
    request<{ server_url: string; participant_token: string; started_at: string | null }>(
      `/interviews/${id}/token`,
      json('POST')
    ),
};
