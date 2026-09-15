// Renders the bundled sample business-card corpus to public/samples/*.jpg.
// The personas here are the same ones the mock extractor returns, so in mock mode the
// fields in the review drawer genuinely match the pixels on the card.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'samples');

/** @type {{file:string,theme:string,degrade?:string,p:Record<string,string>}[]} */
const CARDS = [
  {
    file: 'card_01_northwind.jpg',
    theme: 'ink',
    p: {
      first: 'Priya', last: 'Raghavan', title: 'VP of Partnerships',
      company: 'Northwind Robotics', mark: 'NR',
      location: 'Bengaluru, KA, India', phone: '+91 80 4718 2200',
      email: 'priya@northwind.io', web: 'northwind.io',
    },
  },
  {
    file: 'card_02_cobalt.jpg',
    theme: 'band',
    p: {
      first: 'Marcus', last: 'Oyelaran', title: 'Head of Field Operations',
      company: 'Cobalt Freight Group', mark: 'CF',
      location: 'Lagos, Nigeria', phone: '+234 1 448 9120',
      email: 'm.oyelaran@cobaltfreight.com', web: 'cobaltfreight.com',
    },
  },
  {
    file: 'card_03_kirin.jpg',
    theme: 'mono',
    p: {
      first: 'Hana', last: 'Sato', title: 'Principal Design Engineer',
      company: 'Kirin Optics KK', mark: '光',
      location: 'Yokohama, Japan', phone: '+81 45 663 7712',
      email: 'hana.sato@kirinoptics.jp', web: 'kirinoptics.jp',
    },
  },
  {
    file: 'card_04_meridian.jpg',
    theme: 'serif',
    p: {
      first: 'Elena', last: 'Vasquez', title: 'Director of Clinical Strategy',
      company: 'Meridian Bio', mark: 'MB',
      location: 'Barcelona, Spain', phone: '+34 93 220 1184',
      email: 'e.vasquez@meridianbio.es', web: 'meridianbio.es',
    },
  },
  {
    file: 'card_05_ledgerline.jpg',
    theme: 'split',
    p: {
      first: 'Tom', last: 'Whitfield', title: 'Founder & CEO',
      company: 'Ledgerline', mark: 'L/',
      location: 'Austin, TX, USA', phone: '+1 512 555 0139',
      email: 'tom@ledgerline.co', web: 'ledgerline.co',
    },
  },
  {
    file: 'card_06_atlas.jpg',
    theme: 'band',
    p: {
      first: 'Farid', last: 'Haddad', title: 'Regional Sales Manager',
      company: 'Atlas Cement Co.', mark: 'AC',
      location: 'Dubai, UAE', phone: '+971 4 332 8891',
      email: 'farid.haddad@atlascement.ae', web: 'atlascement.ae',
    },
  },
  {
    file: 'card_07_nordvind.jpg',
    theme: 'ink',
    p: {
      first: 'Greta', last: 'Lindqvist', title: 'Chief Marketing Officer',
      company: 'Nordvind Energi', mark: 'NE',
      location: 'Stockholm, Sweden', phone: '+46 8 559 21 40',
      email: 'greta@nordvind.se', web: 'nordvind.se',
    },
  },
  {
    file: 'card_08_hzprecision.jpg',
    theme: 'mono',
    degrade: 'blur',
    p: {
      first: 'Chen', last: 'Wei', title: 'Procurement Lead',
      company: 'Hanzhou Precision Tools', mark: 'HP',
      location: 'Hangzhou, China', phone: '+86 571 8823 4417',
      email: 'chen.wei@hzprecision.cn', web: 'hzprecision.cn',
    },
  },
  {
    file: 'card_09_northwind_dup.jpg',
    theme: 'split',
    p: {
      first: 'Priya', last: 'Raghavan', title: 'VP of Partnerships',
      company: 'Northwind Robotics', mark: 'NR',
      location: 'Bengaluru, KA, India', phone: '+91 80 4718 2200',
      email: 'priya@northwind.io', web: 'northwind.io',
    },
  },
  {
    file: 'card_10_glare.jpg',
    theme: 'serif',
    degrade: 'glare',
    p: {
      first: 'Yusuf', last: 'Demir', title: 'Logistics Coordinator',
      company: 'Bosphorus Lines', mark: 'BL',
      location: 'Istanbul, Türkiye', phone: '+90 212 447 6630',
      email: 'y.demir@bosphoruslines.tr', web: 'bosphoruslines.tr',
    },
  },
];

const THEMES = {
  ink: (p) => `
    <div class="card" style="background:#12141a;color:#eef0f4">
      <div style="position:absolute;inset:0;background:radial-gradient(120% 140% at 88% 8%, rgba(255,255,255,.07), transparent 60%)"></div>
      <div class="pad" style="display:flex;flex-direction:column;justify-content:space-between;height:100%">
        <div style="display:flex;align-items:center;gap:18px">
          <div style="width:54px;height:54px;border:1.5px solid #6f7891;display:grid;place-items:center;font:600 22px/1 Helvetica,Arial;letter-spacing:1px">${p.mark}</div>
          <div style="font:600 19px/1 Helvetica,Arial;letter-spacing:4.5px;text-transform:uppercase">${p.company}</div>
        </div>
        <div>
          <div style="font:300 46px/1.05 Helvetica,Arial;letter-spacing:-0.6px">${p.first} <span style="font-weight:600">${p.last}</span></div>
          <div style="margin-top:12px;font:400 19px/1 Helvetica,Arial;color:#9aa3b8;letter-spacing:1.6px;text-transform:uppercase">${p.title}</div>
        </div>
        <div style="display:flex;gap:44px;font:400 17px/1.9 Helvetica,Arial;color:#c6ccdb">
          <div>${p.phone}<br>${p.email}</div>
          <div>${p.location}<br>${p.web}</div>
        </div>
      </div>
    </div>`,
  band: (p) => `
    <div class="card" style="background:#fbfaf7;color:#1a1d24">
      <div style="position:absolute;left:0;top:0;bottom:0;width:22px;background:linear-gradient(180deg,#c2410c,#9a1750)"></div>
      <div class="pad" style="padding-left:74px;display:flex;flex-direction:column;justify-content:space-between;height:100%">
        <div style="display:flex;align-items:baseline;gap:14px">
          <div style="font:700 27px/1 Helvetica,Arial;letter-spacing:-0.4px">${p.company}</div>
          <div style="font:600 15px/1 Helvetica,Arial;color:#a1440f;letter-spacing:3px">${p.mark}</div>
        </div>
        <div>
          <div style="font:700 44px/1.05 Helvetica,Arial;letter-spacing:-1px">${p.first} ${p.last}</div>
          <div style="margin-top:10px;font:500 20px/1 Helvetica,Arial;color:#5c6270">${p.title}</div>
        </div>
        <div style="border-top:1px solid #ddd8cf;padding-top:18px;display:flex;justify-content:space-between;font:400 16.5px/1.8 Helvetica,Arial;color:#3b414d">
          <div>${p.phone}<br>${p.email}</div>
          <div style="text-align:right">${p.location}<br>${p.web}</div>
        </div>
      </div>
    </div>`,
  mono: (p) => `
    <div class="card" style="background:#f4f4f1;color:#16181c">
      <div class="pad" style="display:flex;flex-direction:column;justify-content:space-between;height:100%;font-family:'Courier New',monospace">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div style="font:700 21px/1 'Courier New',monospace;letter-spacing:2px">${p.company}</div>
          <div style="width:46px;height:46px;background:#16181c;color:#f4f4f1;display:grid;place-items:center;font:700 19px/1 'Courier New',monospace">${p.mark}</div>
        </div>
        <div>
          <div style="font:700 42px/1.1 'Courier New',monospace;letter-spacing:-1px">${p.first} ${p.last}</div>
          <div style="margin-top:10px;font:400 18px/1 'Courier New',monospace;color:#565b66">${p.title}</div>
        </div>
        <div style="font:400 16px/1.9 'Courier New',monospace;color:#2c3038">
          t ${p.phone}<br>e ${p.email}<br>w ${p.web} &nbsp;·&nbsp; ${p.location}
        </div>
      </div>
    </div>`,
  serif: (p) => `
    <div class="card" style="background:#f7f3ec;color:#20211f">
      <div class="pad" style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;height:100%;text-align:center">
        <div style="font:400 17px/1 Georgia,serif;letter-spacing:7px;text-transform:uppercase;color:#7b6a52">${p.company}</div>
        <div style="width:70px;height:1px;background:#c4b49a"></div>
        <div style="font:400 46px/1.1 Georgia,serif;letter-spacing:-0.5px">${p.first} ${p.last}</div>
        <div style="font:italic 400 21px/1 Georgia,serif;color:#6a6558">${p.title}</div>
        <div style="margin-top:14px;font:400 16.5px/1.9 Georgia,serif;color:#3c3a34">
          ${p.phone} &nbsp;·&nbsp; ${p.email}<br>${p.location} &nbsp;·&nbsp; ${p.web}
        </div>
      </div>
    </div>`,
  split: (p) => `
    <div class="card" style="background:#ffffff;color:#14161b;display:flex">
      <div style="width:38%;background:linear-gradient(160deg,#1c1f27,#3a2030);color:#fff;display:flex;flex-direction:column;justify-content:center;align-items:center;gap:14px">
        <div style="font:700 40px/1 Helvetica,Arial;letter-spacing:-1px">${p.mark}</div>
        <div style="font:500 13px/1.5 Helvetica,Arial;letter-spacing:3px;text-transform:uppercase;text-align:center;max-width:78%;color:#cbd0dd">${p.company}</div>
      </div>
      <div style="flex:1;padding:52px 46px;display:flex;flex-direction:column;justify-content:center;gap:10px">
        <div style="font:700 40px/1.08 Helvetica,Arial;letter-spacing:-1px">${p.first} ${p.last}</div>
        <div style="font:500 18px/1 Helvetica,Arial;color:#c2410c;letter-spacing:0.6px">${p.title}</div>
        <div style="margin-top:22px;font:400 16.5px/1.95 Helvetica,Arial;color:#3b3f49">
          ${p.phone}<br>${p.email}<br>${p.web}<br>${p.location}
        </div>
      </div>
    </div>`,
};

const DEGRADE = {
  blur: 'filter:blur(1.7px) contrast(.82) brightness(1.06);',
  glare: '',
};

const page_html = (card) => `<!doctype html><meta charset="utf-8">
<style>
  *{box-sizing:border-box;margin:0}
  body{margin:0;background:#20222a;width:1120px;height:680px;display:grid;place-items:center}
  .frame{position:relative;width:1050px;height:600px;overflow:hidden;border-radius:10px;${DEGRADE[card.degrade] ?? ''}}
  .card{position:absolute;inset:0;overflow:hidden}
  .pad{padding:52px 56px}
  .grain{position:absolute;inset:0;opacity:.16;background-image:repeating-linear-gradient(0deg,rgba(0,0,0,.14) 0 1px,transparent 1px 3px)}
  .glare{position:absolute;inset:-20%;background:linear-gradient(112deg,transparent 32%,rgba(255,255,255,.85) 46%,rgba(255,255,255,.35) 54%,transparent 66%);}
</style>
<div class="frame">
  ${THEMES[card.theme](card.p)}
  <div class="grain"></div>
  ${card.degrade === 'glare' ? '<div class="glare"></div>' : ''}
</div>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1120, height: 680 }, deviceScaleFactor: 1 });
await mkdir(OUT, { recursive: true });

for (const card of CARDS) {
  await page.setContent(page_html(card));
  const el = await page.waitForSelector('.frame');
  await el.screenshot({ path: join(OUT, card.file), type: 'jpeg', quality: 88 });
  process.stdout.write(`wrote ${card.file}\n`);
}

await writeFile(
  join(OUT, 'manifest.json'),
  `${JSON.stringify({ files: CARDS.map((c) => c.file) }, null, 2)}\n`,
);
await browser.close();
