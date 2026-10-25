/** @type {import('next').NextConfig} */
const nextConfig = {
  // Phase 1: dev mode only, no static export yet
  // output: 'export', // Will enable after Phase 1 for Web standalone build
  images: {
    unoptimized: true, // Required for static export later
  },
  // Required for Electron: allow loading from file:// or localhost
  assetPrefix: process.env.NEXT_ASSET_PREFIX || undefined,
};

export default nextConfig;