const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  typescript: { ignoreBuildErrors: false },
  // lib/version.ts reads ../version.json, the repository's single version
  // record. Turbopack (the default bundler since Next 16) only resolves files
  // under its root, so the root is the repository, not frontend/.
  turbopack: { root: path.join(__dirname, "..") },
};

module.exports = nextConfig;
