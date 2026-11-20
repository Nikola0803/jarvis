/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/jarvis/:path*",
        destination: "http://127.0.0.1:8080/api/:path*",
      },
    ];
  },
  webpack(config, { isServer }) {
    if (!isServer) {
      config.resolve.alias = {
        ...config.resolve.alias,
        "onnxruntime-node$": false,
      };
    }
    return config;
  },
};

export default nextConfig;
