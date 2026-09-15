import type { Confidence } from '../types';

/** How cleanly the mock "VLM" reads a card. Drives confidence, flags and terminal status. */
export type Profile = 'clean' | 'soft' | 'low' | 'fail';

export interface Persona {
  first_name: string;
  last_name: string;
  job_title: string;
  company: string;
  location: string;
  phone: string;
  phone_e164: string;
  email: string;
  website: string;
  profile: Profile;
}

/**
 * These are the exact people printed on public/samples/*.jpg (rendered by
 * scripts/gen-samples.mjs), so the side-by-side review screen shows fields that genuinely
 * match the pixels.
 */
export const PERSONAS: Persona[] = [
  {
    first_name: 'Priya',
    last_name: 'Raghavan',
    job_title: 'VP of Partnerships',
    company: 'Northwind Robotics',
    location: 'Bengaluru, KA, India',
    phone: '+91 80 4718 2200',
    phone_e164: '+918047182200',
    email: 'priya@northwind.io',
    website: 'northwind.io',
    profile: 'clean',
  },
  {
    first_name: 'Marcus',
    last_name: 'Oyelaran',
    job_title: 'Head of Field Operations',
    company: 'Cobalt Freight Group',
    location: 'Lagos, Nigeria',
    phone: '+234 1 448 9120',
    phone_e164: '+23414489120',
    email: 'm.oyelaran@cobaltfreight.com',
    website: 'cobaltfreight.com',
    profile: 'clean',
  },
  {
    first_name: 'Hana',
    last_name: 'Sato',
    job_title: 'Principal Design Engineer',
    company: 'Kirin Optics KK',
    location: 'Yokohama, Japan',
    phone: '+81 45 663 7712',
    phone_e164: '+814566377712',
    email: 'hana.sato@kirinoptics.jp',
    website: 'kirinoptics.jp',
    profile: 'clean',
  },
  {
    first_name: 'Elena',
    last_name: 'Vasquez',
    job_title: 'Director of Clinical Strategy',
    company: 'Meridian Bio',
    location: 'Barcelona, Spain',
    phone: '+34 93 220 1184',
    phone_e164: '+34932201184',
    email: 'e.vasquez@meridianbio.es',
    website: 'meridianbio.es',
    profile: 'clean',
  },
  {
    first_name: 'Tom',
    last_name: 'Whitfield',
    job_title: 'Founder & CEO',
    company: 'Ledgerline',
    location: 'Austin, TX, USA',
    phone: '+1 512 555 0139',
    phone_e164: '+15125550139',
    email: 'tom@ledgerline.co',
    website: 'ledgerline.co',
    profile: 'clean',
  },
  {
    first_name: 'Farid',
    last_name: 'Haddad',
    job_title: 'Regional Sales Manager',
    company: 'Atlas Cement Co.',
    location: 'Dubai, UAE',
    phone: '+971 4 332 8891',
    phone_e164: '+97143328891',
    email: 'farid.haddad@atlascement.ae',
    website: 'atlascement.ae',
    profile: 'soft',
  },
  {
    first_name: 'Greta',
    last_name: 'Lindqvist',
    job_title: 'Chief Marketing Officer',
    company: 'Nordvind Energi',
    location: 'Stockholm, Sweden',
    phone: '+46 8 559 21 40',
    phone_e164: '+4685592140',
    email: 'greta@nordvind.se',
    website: 'nordvind.se',
    profile: 'clean',
  },
  {
    first_name: 'Chen',
    last_name: 'Wei',
    job_title: 'Procurement Lead',
    company: 'Hanzhou Precision Tools',
    location: 'Hangzhou, China',
    phone: '+86 571 8823 4417',
    phone_e164: '+8657188234417',
    email: 'chen.wei@hzprecision.cn',
    website: 'hzprecision.cn',
    profile: 'low',
  },
  {
    first_name: 'Yusuf',
    last_name: 'Demir',
    job_title: 'Logistics Coordinator',
    company: 'Bosphorus Lines',
    location: 'Istanbul, Türkiye',
    phone: '+90 212 447 6630',
    phone_e164: '+902124476630',
    email: 'y.demir@bosphoruslines.tr',
    website: 'bosphoruslines.tr',
    profile: 'fail',
  },
  {
    first_name: 'Amara',
    last_name: 'Nwosu',
    job_title: 'Head of Supply Chain',
    company: 'Kestrel Materials',
    location: 'Accra, Ghana',
    phone: '+233 30 273 4410',
    phone_e164: '+233302734410',
    email: 'amara@kestrelmaterials.com',
    website: 'kestrelmaterials.com',
    profile: 'clean',
  },
  {
    first_name: 'Jonas',
    last_name: 'Berger',
    job_title: 'Technical Account Manager',
    company: 'Halbach Systems GmbH',
    location: 'Munich, Germany',
    phone: '+49 89 2000 7741',
    phone_e164: '+498920007741',
    email: 'j.berger@halbach-systems.de',
    website: 'halbach-systems.de',
    profile: 'soft',
  },
  {
    first_name: 'Sofia',
    last_name: 'Marchetti',
    job_title: 'Partner, Corporate Finance',
    company: 'Vallone Capital',
    location: 'Milan, Italy',
    phone: '+39 02 4512 8890',
    phone_e164: '+390245128890',
    email: 's.marchetti@vallonecapital.it',
    website: 'vallonecapital.it',
    profile: 'clean',
  },
];

/** Filenames in the bundled corpus map to a fixed persona so the demo is reproducible. */
export const SAMPLE_MAP: Record<string, number> = {
  'card_01_northwind.jpg': 0,
  'card_02_cobalt.jpg': 1,
  'card_03_kirin.jpg': 2,
  'card_04_meridian.jpg': 3,
  'card_05_ledgerline.jpg': 4,
  'card_06_atlas.jpg': 5,
  'card_07_nordvind.jpg': 6,
  'card_08_hzprecision.jpg': 7,
  'card_09_northwind_dup.jpg': 0,
  'card_10_glare.jpg': 8,
};

const CONFIDENCE_BY_PROFILE: Record<Profile, Confidence> = {
  clean: {
    first_name: 0.97,
    last_name: 0.96,
    job_title: 0.93,
    company: 0.98,
    location: 0.9,
    phone: 0.99,
    email: 0.99,
    website: 0.94,
  },
  soft: {
    first_name: 0.91,
    last_name: 0.88,
    job_title: 0.61,
    company: 0.86,
    location: 0.49,
    phone: 0.95,
    email: 0.72,
    website: 0.44,
  },
  low: {
    first_name: 0.64,
    last_name: 0.58,
    job_title: 0.41,
    company: 0.71,
    location: 0.52,
    phone: 0.83,
    email: 0.66,
    website: 0.33,
  },
  fail: {
    first_name: 0,
    last_name: 0,
    job_title: 0,
    company: 0,
    location: 0,
    phone: 0,
    email: 0,
    website: 0,
  },
};

/** Deterministic 32-bit string hash so unknown uploads always extract the same way. */
export function hashString(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function confidenceFor(profile: Profile, seed: number): Confidence {
  const base = CONFIDENCE_BY_PROFILE[profile];
  const out = {} as Confidence;
  let i = 0;
  for (const key of Object.keys(base) as (keyof Confidence)[]) {
    // ±0.02 of deterministic jitter keeps the numbers from looking hand-typed.
    const jitter = (((seed >>> (i * 3)) & 0xff) / 255 - 0.5) * 0.04;
    out[key] = base[key] === 0 ? 0 : Math.min(0.995, Math.max(0.05, base[key] + jitter));
    i += 1;
  }
  return out;
}

export function pickPersona(filename: string): { persona: Persona; seed: number } {
  const seed = hashString(filename.toLowerCase());
  const mapped = SAMPLE_MAP[filename];
  if (mapped !== undefined) {
    return { persona: PERSONAS[mapped] as Persona, seed };
  }
  // Unknown uploads: spread across the pool, and let ~1 in 10 fail and ~1 in 6 read badly
  // so the failure and review states are reachable without the sample corpus.
  const persona = PERSONAS[seed % PERSONAS.length] as Persona;
  const bucket = (seed >>> 8) % 100;
  const profile: Profile = bucket < 8 ? 'fail' : bucket < 22 ? 'low' : bucket < 34 ? 'soft' : 'clean';
  return { persona: { ...persona, profile }, seed };
}
