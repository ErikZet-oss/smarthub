import { jwtVerify } from "jose";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { SMARTHUB_SESSION_COOKIE } from "@/lib/auth-config";

export async function GET() {
  const secretRaw = process.env.SMARTHUB_AUTH_SECRET;
  if (!secretRaw || secretRaw.length < 16) {
    return NextResponse.json({
      token: null,
      username: null,
      userId: null,
      isAdmin: false,
      error: "config",
    });
  }

  const jar = await cookies();
  const raw = jar.get(SMARTHUB_SESSION_COOKIE)?.value;
  if (!raw) {
    return NextResponse.json({
      token: null,
      username: null,
      userId: null,
      isAdmin: false,
    });
  }

  try {
    const { payload } = await jwtVerify(
      raw,
      new TextEncoder().encode(secretRaw),
    );
    const uidRaw = payload.uid;
    const userId =
      typeof uidRaw === "number"
        ? uidRaw
        : uidRaw != null
          ? Number(uidRaw)
          : null;
    return NextResponse.json({
      token: raw,
      username: typeof payload.sub === "string" ? payload.sub : null,
      userId: userId != null && Number.isFinite(userId) ? userId : null,
      isAdmin: payload.role === "admin",
    });
  } catch {
    return NextResponse.json({
      token: null,
      username: null,
      userId: null,
      isAdmin: false,
    });
  }
}
