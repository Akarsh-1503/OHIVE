import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  // Three.js ships untranspiled ESM in a few subpaths; letting Next transpile it keeps
  // the standalone server build from choking on them.
  transpilePackages: ['three'],
  // The e2e run drives the dev server over 127.0.0.1 rather than localhost.
  allowedDevOrigins: ['127.0.0.1'],
};

export default nextConfig;
