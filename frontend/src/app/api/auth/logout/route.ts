import { NextResponse } from "next/server";

import { SMARTHUB_SESSION_COOKIE } from "@/lib/auth-config";

export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SMARTHUB_SESSION_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return res;
}
