/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow the app to be served from a subdirectory if needed
  // basePath: '/atlas',

  // API URL configured via env — falls back to localhost for dev
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
    NEXT_PUBLIC_USE_MOCK: process.env.NEXT_PUBLIC_USE_MOCK || 'false',
  },
};

module.exports = nextConfig;
