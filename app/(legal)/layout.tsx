export default function LegalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-6 py-10 text-sm leading-7 [&_h1]:text-3xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold [&_a]:underline [&_a]:underline-offset-4">
      {children}
    </section>
  );
}
