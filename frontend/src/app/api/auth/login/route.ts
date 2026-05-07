import { SignJWT } from "jose";
import { NextResponse } from "next/server";

import { normalizeApiOrigin } from "@/lib/api-origin";
import { SMARTHUB_SESSION_COOKIE } from "@/lib/auth-config";

export async function POST(request: Request) {
  const secretRaw = process.env.SMARTHUB_AUTH_SECRET;

  if (!secretRaw || secretRaw.length < 16) {

    return NextResponse.json(

      {

        error:

          "Prihlásenie nie je nakonfigurované. Doplň SMARTHUB_AUTH_SECRET (min. 16 znakov) v .env.local a rovnakú hodnotu na FastAPI.",

      },

      { status: 500 },

    );

  }



  let body: { username?: string; password?: string };

  try {

    body = (await request.json()) as { username?: string; password?: string };

  } catch {

    return NextResponse.json({ error: "Neplatné telo požiadavky." }, { status: 400 });

  }



  const username = String(body.username ?? "").trim();

  const password = String(body.password ?? "");



  if (!username || !password) {

    return NextResponse.json(

      { error: "Vyplň prihlasovacie meno a heslo." },

      { status: 400 },

    );

  }



  const apiBase = normalizeApiOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);


  let verifyRes: Response;

  try {

    verifyRes = await fetch(`${apiBase}/api/auth/smarthub-login`, {

      method: "POST",

      headers: { "Content-Type": "application/json" },

      body: JSON.stringify({ username, password }),

    });

  } catch {

    return NextResponse.json(

      {

        error: `API nedostupné (${apiBase}). Spusti backend (uvicorn) a skontroluj NEXT_PUBLIC_API_BASE_URL.`,

      },

      { status: 502 },

    );

  }



  const authPayload = (await verifyRes.json().catch(() => ({}))) as {

    id?: number;

    username?: string;

    is_admin?: boolean;

    detail?: unknown;

  };



  if (!verifyRes.ok) {

    if (verifyRes.status === 404) {

      return NextResponse.json(

        {

          error:

            `Na ${apiBase} nie je Smarthub login (HTTP 404). Na tomto porte často beží iná aplikácia. Zastav ju alebo spusti Smarthub API z priečinka backend (uvicorn) a v .env.local nastav NEXT_PUBLIC_API_BASE_URL na správnu adresu (napr. iný port: http://127.0.0.1:8001).`,

        },

        { status: 502 },

      );

    }

    const detail =

      typeof authPayload.detail === "string"

        ? authPayload.detail

        : verifyRes.status === 401

          ? "Nesprávne prihlasovacie meno alebo heslo."

          : `API odpovedalo chybou (HTTP ${verifyRes.status}).`;

    return NextResponse.json(

      { error: detail },

      { status: verifyRes.status === 401 ? 401 : 502 },

    );

  }



  const uid = authPayload.id;

  const uname = authPayload.username ?? username;

  if (typeof uid !== "number" || !Number.isFinite(uid)) {

    return NextResponse.json(

      { error: "API vrátilo neplatné údaje používateľa." },

      { status: 502 },

    );

  }



  const secret = new TextEncoder().encode(secretRaw);

  const token = await new SignJWT({

    sub: uname,

    role: authPayload.is_admin ? "admin" : "user",

    uid,

  })

    .setProtectedHeader({ alg: "HS256" })

    .setIssuedAt()

    .setExpirationTime("7d")

    .sign(secret);



  const res = NextResponse.json({ ok: true });

  res.cookies.set(SMARTHUB_SESSION_COOKIE, token, {

    httpOnly: true,

    secure: process.env.NODE_ENV === "production",

    sameSite: "lax",

    path: "/",

    maxAge: 60 * 60 * 24 * 7,

  });

  return res;

}

