// Mirrors _contracts/assignment1-api.md. Field names are snake_case because the wire
// format is snake_case; translating at the boundary would only add a mapping layer to
// keep in sync with the contract.

export type LeadStatus = 'queued' | 'processing' | 'completed' | 'needs_review' | 'failed';
export type BatchStatus = 'queued' | 'processing' | 'completed' | 'partial' | 'failed';

export const LEAD_FIELDS = [
  'first_name',
  'last_name',
  'job_title',
  'company',
  'location',
  'phone',
  'email',
  'website',
] as const;

export type LeadField = (typeof LEAD_FIELDS)[number];

export const FIELD_LABELS: Record<LeadField, string> = {
  first_name: 'First name',
  last_name: 'Last name',
  job_title: 'Job title',
  company: 'Company',
  location: 'Location',
  phone: 'Phone',
  email: 'Email',
  website: 'Website',
};

export type Confidence = Record<LeadField, number>;

export interface Lead {
  card_id: string;
  batch_id: string;
  filename: string;
  status: LeadStatus;

  first_name: string | null;
  last_name: string | null;
  job_title: string | null;
  company: string | null;
  location: string | null;
  phone: string | null;
  phone_e164: string | null;
  email: string | null;
  website: string | null;

  confidence: Confidence;
  overall_confidence: number;
  quality_flags: string[];
  duplicate_of: string | null;
  raw_text: string | null;
  edited: boolean;
  processing_ms: number | null;
  error: string | null;
  created_at: string;
}

export interface Batch {
  batch_id: string;
  status: BatchStatus;
  total: number;
  completed: number;
  failed: number;
  pending: number;
  created_at: string;
  finished_at: string | null;
  elapsed_ms: number;
  leads: Lead[];
}

export interface Health {
  status: string;
  version: string;
  vlm: {
    provider: 'modal' | 'openai_compatible' | 'stub';
    model: string;
    endpoint_reachable: boolean;
    warm: boolean;
    last_latency_ms: number | null;
  };
  uptime_s: number;
}

export interface BatchProgress {
  completed: number;
  failed: number;
  total: number;
  elapsed_ms: number;
}

export interface CardStarted {
  card_id: string;
  filename: string;
}

export interface CardFailed {
  card_id: string;
  error: string;
}

export interface ApiErrorBody {
  detail: string;
  code?: string;
}

export const QUALITY_FLAG_LABELS: Record<string, string> = {
  low_resolution: 'Low resolution',
  blurry: 'Blurry',
  glare: 'Glare',
  no_email: 'No email found',
  no_phone: 'No phone found',
  no_website: 'No website found',
  duplicate: 'Duplicate card',
  partial_crop: 'Card is cropped',
  handwritten: 'Handwriting detected',
};
