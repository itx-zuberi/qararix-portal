# Deploy steps (Windows PowerShell)

## 0. Pehle: purane secrets rotate karein
Supabase password reset karein, naya SECRET_KEY banayein (neeche), aur naya admin password chunein.

## 1. Naya SECRET_KEY banayein
    python -c "import secrets; print(secrets.token_hex(32))"

## 2. Env variables: automatic way -> powershell -ExecutionPolicy Bypass -File .\set-env.ps1  (ya neeche manual)
## 2b. Vercel > Project > Settings > Environment Variables (Production)
    DATABASE_URL            (Supabase Transaction pooler, port 6543, password URL-encoded, @ = %40)
    SECRET_KEY              (step 1 wali value)
    QARARIX_ADMIN_PASSWORD  (strong password)
    QARARIX_HTTPS           1
Do NOT set QARARIX_DEBUG in production.

## 3. Code push karein
First time only (agar folder git repo nahi hai):
    git init
    git branch -M main
    git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
Phir har dafa:
    powershell -ExecutionPolicy Bypass -File .\deploy.ps1

## 4. Vercel > Deployments
Naya deployment (naya ID) Ready hone ka intezar karein, phir site kholein.

## Speed notes
- vercel.json puts the function in Sydney (syd1), next to the Supabase database (ap-southeast-2).
- The database connection is re-used between requests; settings are cached for 30 s.
- If your Supabase project is in another region, change "syd1" in vercel.json to the nearest Vercel region.
