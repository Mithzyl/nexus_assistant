import { AuthProvider } from "@/components/mobile/providers";
import { LoginScreen } from "@/components/mobile/login-screen";

export default function LoginPage() {
  return (
    <AuthProvider>
      <LoginScreen />
    </AuthProvider>
  );
}
