# Dockerfile for React Frontend (Vite + Tailwind + shadcn/ui)

# 1. Build stage
FROM node:20-alpine AS builder
WORKDIR /app

# Copy package files and install deps
COPY package*.json ./
RUN npm install

# Copy source and build
COPY . .
ENV VITE_API_URL=http://10.2.0.204:8000
RUN npm run build

# 2. Serve stage with nginx
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY ./nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

