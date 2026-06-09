import unittest
from shutil import which

from book_builder import (
    _convert_markdown_to_latex,
    _prepare_markdown_for_latex,
    _sanitize_final_document_math,
    _sanitize_section_tex,
)


class BookMarkdownMathTests(unittest.TestCase):
    def test_prepare_markdown_wraps_inline_index_math_before_pandoc(self):
        raw = (
            "a_{ij} = cos α_{ij}. "
            "v′_i = a_{ij}\\, v_j, kde ∂_x u ⇒ 0, Γ_elem = ∫_S ω⃗·n dS "
            "a Γ_elem \\xrightarrow[Δ→0]{} γ·Δs. A^T A = I."
        )

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn("$a_{ij}$", prepared)
        self.assertIn("$\\alpha_{ij}$", prepared)
        self.assertIn("$v'_i$", prepared)
        self.assertIn("$v_j$", prepared)
        self.assertIn("$\\partial_x$", prepared)
        self.assertIn("$\\Rightarrow$", prepared)
        self.assertIn("$\\Gamma_{elem}$", prepared)
        self.assertIn("$\\int_S$", prepared)
        self.assertIn("$\\vec{\\omega}$", prepared)
        self.assertIn("$\\xrightarrow[\\Delta\\rightarrow 0]{}$", prepared)
        self.assertIn("$A^T$", prepared)
        self.assertNotIn("\\,", prepared)

    def test_prepare_markdown_preserves_citations_and_code_spans(self):
        raw = (
            "Pouzij `a_{ij}` v kodu, ale v textu odkazuj na \\cite{kb_pr01_docx_d34b918c_chunk_4} "
            "a definuj α_{ij}."
        )

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn("`a_{ij}`", prepared)
        self.assertIn("\\cite{kb_pr01_docx_d34b918c_chunk_4}", prepared)
        self.assertIn("$\\alpha_{ij}$", prepared)

    def test_prepare_markdown_keeps_integral_command_intact(self):
        raw = (
            r"Gamma_elem = \int_{-\delta/2}^{+\delta/2} \omega_n(y)\,dy "
            r"\xrightarrow[\delta\to0]{} \gamma \cdot \Delta s. "
            r"\int_\Omega f(x)\,dV_x = \int_\Xi g(\xi)\,d\xi^1."
        )

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\int_{-\delta/2}^{+\delta/2}$", prepared)
        self.assertIn(r"$\int_\Omega$", prepared)
        self.assertIn(r"$\int_\Xi$", prepared)
        self.assertNotIn(r"$\in$ $t_{-", prepared)
        self.assertNotIn(r"$\in$ t_", prepared)

    def test_prepare_markdown_separates_in_membership_from_following_symbol(self):
        raw = r"x \inS_{wall}, zatimco \infty zustava nedotceno."

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\in$ $S_{wall}$", prepared)
        self.assertIn(r"$\infty$", prepared)
        self.assertNotIn(r"\inS", prepared)

    def test_prepare_markdown_separates_quad_from_following_symbol(self):
        raw = r"\quadv_\theta + \qquadv_r"

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\quad$ $v_\theta$", prepared)
        self.assertIn(r"$\qquad$ $v_r$", prepared)
        self.assertNotIn(r"\quadv", prepared)

    def test_prepare_markdown_wraps_greek_subscripts_as_single_tokens(self):
        raw = r"h_\phi = r, h_\theta = r, v_\phi = 0, A^{\mathrm T}A = I."

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$h_\phi$", prepared)
        self.assertIn(r"$h_\theta$", prepared)
        self.assertIn(r"$v_\phi$", prepared)
        self.assertIn(r"A^{\mathrm{T}}", prepared)
        self.assertNotIn(r"$h_{$\phi$}$", prepared)
        self.assertNotIn(r"$A^{\)$", prepared)

    def test_prepare_markdown_keeps_indexed_bounds_inside_integrals(self):
        raw = r"\int_{S_2} \omega \cdot n dS = \int_{S_1} \omega \cdot n dS."

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\int_{S_2}$", prepared)
        self.assertIn(r"$\int_{S_1}$", prepared)
        self.assertNotIn(r"$\int_{$S_2$}$", prepared)
        self.assertNotIn(r"$\int_{$S_1$}$", prepared)

    def test_prepare_markdown_wraps_tex_math_functions_used_in_text(self):
        raw = r"\alpha_{ij}=\cos(\angle(x'_i,x_j))"

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\alpha_{ij}$", prepared)
        self.assertIn(r"$\cos$", prepared)
        self.assertIn(r"$\angle$", prepared)
        self.assertIn(r"$x'_i$", prepared)
        self.assertIn(r"$x_j$", prepared)
        self.assertNotIn(r"\cos(\angle(", prepared)

    def test_prepare_markdown_wraps_dot_accent_commands(self):
        raw = r"\dot{m}=\rho Q, \ddot{x}=0."

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\dot{m}$", prepared)
        self.assertIn(r"$\ddot{x}$", prepared)

    def test_prepare_markdown_wraps_tfrac_commands(self):
        raw = r"\mathbf{v}\cdot\partial_s\mathbf{v}=\tfrac12\partial_s|\mathbf{v}|^2."

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\tfrac{1}{2}$", prepared)

    def test_prepare_markdown_reconstructs_fragmented_accent_commands(self):
        raw = r"\(\hat\)\{$\mathbf{t}\}$ a \(\vec\)\{\(\omega\)\}"

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"\(\hat{\mathbf{t}}\)", prepared)
        self.assertIn(r"\(\vec{\omega}\)", prepared)
        self.assertNotIn(r"\(\hat\)\{", prepared)

    def test_prepare_markdown_strips_inner_spaces_from_inline_dollar_math(self):
        raw = r"$ \displaystyle \Gamma \;=\; \oint_{K} \gamma_{K}(s)\, ds $"

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\displaystyle \Gamma \;=\; \oint_{K} \gamma_{K}(s)\, ds$", prepared)
        self.assertNotIn(r"$ \displaystyle", prepared)
        self.assertNotIn(r" ds $", prepared)

    def test_prepare_markdown_strips_standalone_left_right_sizing_commands(self):
        raw = r"\left(U_\infty-\frac{\alpha}{r^2}\right)"

        prepared = _prepare_markdown_for_latex(raw)

        self.assertIn(r"$\frac{\alpha}{r^2}$", prepared)
        self.assertNotIn(r"\left", prepared)
        self.assertNotIn(r"\right", prepared)

    def test_prepare_markdown_preserves_raw_equation_environment(self):
        raw = (
            r"\begin{equation}\label{eq:test} \frac{\partial\rho}{\partial t} + "
            r"\nabla\cdot(\rho\mathbf{v}) = 0. \end{equation}"
        )

        prepared = _prepare_markdown_for_latex(raw)

        self.assertEqual(raw, prepared)
        self.assertNotIn(r"$\frac", prepared)

    def test_sanitize_section_tex_rewrites_bold_greek_for_unicode_math(self):
        raw = r"\[ \dot{\boldsymbol{\xi}} = \mathbf{A}\,\boldsymbol{\xi} \]"

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\dot{\symbfit{\xi}}", tex)
        self.assertIn(r"\symbfit{\xi}", tex)
        self.assertNotIn(r"\boldsymbol{", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_rewrites_unbraced_bold_greek_for_unicode_math(self):
        raw = r"\[ \frac{D\boldsymbol\omega}{Dt} = (\boldsymbol\omega\cdot\nabla)\mathbf v \]"

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\frac{D\symbfit{\omega}}{Dt}", tex)
        self.assertIn(r"(\symbfit{\omega}\cdot\nabla)\mathbf{v}", tex)
        self.assertNotIn(r"\boldsymbol\omega", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_normalizes_styled_inline_math_wrappers(self):
        raw = r"Axiální vektor \symbfit{$\omega$} a tenzor P = p \(\mathbf{I}\) - \symbfit{$\tau})$."

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"$\symbfit{\omega}$", tex)
        self.assertIn(r"$\symbfit{\tau}$)", tex)
        self.assertNotIn(r"\symbfit{$", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_rewrites_boldsymbol_inline_math_delimiters(self):
        raw = r"Vektor víru \boldsymbol\(\omega\) a tenzor \bm$\tau$."

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\(\symbfit{\omega}\)", tex)
        self.assertIn(r"$\symbfit{\tau}$", tex)
        self.assertNotIn(r"\boldsymbol\(", tex)
        self.assertNotIn(r"\bm$", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_wraps_symbfit_outside_math_mode(self):
        raw = r"kde \symbfit{\zeta}=\(\nabla\)\times\mathbf{v}."

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"$\symbfit{\zeta}$", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_reconstructs_fragmented_style_commands(self):
        raw = r"kde \(\rho\) je hustota, \(\mathbf\) v rychlostni pole a \(\mathrm\) d element delky."

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\(\mathbf{v}\)", tex)
        self.assertIn(r"\(\mathrm{d}\)", tex)
        self.assertNotIn(r"\(\mathbf\) v", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_reconstructs_fragmented_fraction_commands(self):
        raw = r"\(\frac\)\{$\partial$\(\sigma_{xx}\)\}\{$\partial x\} + \(\frac\)\{d\(\mathbf{r}\)\}\{ds\}"

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\(\frac{\partial\sigma_{xx}}{\partial x}\)", tex)
        self.assertIn(r"\(\frac{d\mathbf{r}}{ds}\)", tex)
        self.assertNotIn(r"\(\frac\)\{", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_reconstructs_fragmented_accent_commands(self):
        raw = r"kde \(\hat\)\{$\mathbf{t}\}$ je tecna a \(\vec\)\{\(\omega\)\} znaci orientaci."

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\(\hat{\mathbf{t}}\)", tex)
        self.assertIn(r"\(\vec{\omega}\)", tex)
        self.assertNotIn(r"\(\hat\)\{", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_moves_trailing_citation_out_of_display_math(self):
        raw = r"\[ \mathbf{v}=\nabla\Phi. \cite{kb_example} \]"

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\mathbf{v}=\nabla\Phi. \] \cite{kb_example}", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_merges_interleaved_inline_math_fragments(self):
        raw = r"\(e^{i\)$\theta$\(}\) a \(u^{(\)$\Gamma$\()}_j\)"

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\(e^{i\theta}\)", tex)
        self.assertIn(r"\(u^{(\Gamma)}_j\)", tex)
        self.assertNotIn(r"\)$\theta$\(", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_section_tex_unescapes_math_subscripts_after_commands(self):
        raw = r"(napr. \(I_{ik}\)=\(\int_L\mathbf{n}\)(\(\mu_i\)) \(\cdot\mathbf{K}_\gamma\)(r(\(\mu_i\)),s) \varphi\_k(s) ds)"

        tex, issues = _sanitize_section_tex(raw)

        self.assertIn(r"\varphi_k", tex)
        self.assertNotIn(r"\varphi\_k", tex)
        self.assertIn("sanitized_content", issues)

    def test_sanitize_final_document_math_normalizes_unicode_math_symbols_in_body(self):
        raw = (
            "\\documentclass{book}\n"
            "\\begin{document}\n"
            "Prechod i↔j, A↦B, prostor ℝ^3, k ∈ ℤ, vektor ζ v souradnici η, "
            "v_r ∝ 1/r, δ/L ≪ 1, A ⊗ B a ∬_S f dS. "
            "Plati a ∼ b, A ≍ B, x ∥ y, x ≫ y, x ≲ y, dℓ a m³ s⁻¹.\n"
            "\\end{document}\n"
        )

        tex = _sanitize_final_document_math(raw)

        self.assertIn(r"\documentclass{book}", tex)
        self.assertIn(r"$\leftrightarrow$", tex)
        self.assertIn(r"$\mapsto$", tex)
        self.assertIn(r"$\mathbb{R}^3$", tex)
        self.assertIn(r"$\in$", tex)
        self.assertIn(r"$\mathbb{Z}$", tex)
        self.assertIn(r"$\zeta$", tex)
        self.assertIn(r"$\eta$", tex)
        self.assertIn(r"$\propto 1/r$", tex)
        self.assertIn(r"$\delta/L \ll 1$", tex)
        self.assertIn(r"$\otimes$", tex)
        self.assertIn(r"$\iint_S$", tex)
        self.assertIn(r"$\sim$", tex)
        self.assertIn(r"$\asymp$", tex)
        self.assertIn(r"$\parallel$", tex)
        self.assertIn(r"$\gg$", tex)
        self.assertIn(r"$\lesssim$", tex)
        self.assertIn(r"$\ell$", tex)
        self.assertIn(r"m\textsuperscript{3}", tex)
        self.assertIn(r"s\textsuperscript{-1}", tex)

    @unittest.skipUnless(which("pandoc"), "pandoc required")
    def test_convert_markdown_preserves_display_math_with_backslash_delimiters(self):
        raw = (
            "Baze ve valcovych souradnicich: "
            r"\[ \mathbf{e}_r(\varphi)=\cos\varphi\,\mathbf{e}_x+\sin\varphi\,\mathbf{e}_y \]"
        )

        tex = _convert_markdown_to_latex(raw)

        self.assertIn(r"\[ \mathbf{e}_r(\varphi)=\cos\varphi\,\mathbf{e}_x+\sin\varphi\,\mathbf{e}_y \]", tex)
        self.assertNotIn(r"\emph{", tex)
        self.assertNotIn(r"\textsuperscript", tex)


if __name__ == "__main__":
    unittest.main()
