/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  experimental: { outputFileTracingRoot: new URL('../..', import.meta.url).pathname },
  images: {
    unoptimized: true, // Required for static export later
  },
  // Required for Electron: allow loading from file:// or localhost
  assetPrefix: process.env.NEXT_ASSET_PREFIX || undefined,
};

export default nextConfig;