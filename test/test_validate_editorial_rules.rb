# frozen_string_literal: true

require "minitest/autorun"
require "open3"
require "tmpdir"
require "fileutils"

class ValidateEditorialRulesTest < Minitest::Test
  REPO = File.expand_path("..", __dir__)
  SCRIPT = File.join(REPO, "scripts", "validate_editorial_rules.rb")

  def with_fixture
    Dir.mktmpdir("oneplate-editorial-") do |root|
      FileUtils.mkdir_p(File.join(root, "_posts"))
      FileUtils.mkdir_p(File.join(root, "assets", "images"))
      yield root
    end
  end

  def write_post(root, name, lang:, key:, body:, images: [], rules_version: 1)
    permalink = lang == "en" ? "/en/posts/#{key}.html" : "/posts/#{key}.html"
    image_yaml = images.empty? ? "" : "ai_generated_images:\n#{images.map { |image| "  - #{image}" }.join("\n")}\n"
    File.write(File.join(root, "_posts", name), <<~POST)
      ---
      title: Test
      series: easy-ai
      lang: #{lang}
      translation_key: #{key}
      permalink: #{permalink}
      published: true
      editorial_rules: #{rules_version}
      published_at: 2026-09-10
      #{image_yaml}---
      #{body}
    POST
  end

  def run_validator(root, *args)
    Open3.capture3({ "ONEPLATE_ROOT" => root }, "ruby", SCRIPT, *args)
  end

  def test_report_mode_warns_but_strict_mode_rejects_missing_image_disclosure
    with_fixture do |root|
      File.write(File.join(root, "assets", "images", "scene.svg"), "<svg><text>no label</text></svg>")
      body = <<~HTML
        <h2>문장형 소제목.</h2>
        <figure><img src="{{ '/assets/images/scene.svg' | relative_url }}" alt="scene"></figure>
        <h2>출처.</h2><ul class="sources"><li><a href="https://example.com">official</a></li></ul>
      HTML
      write_post(root, "2026-09-10-test.md", lang: "ko", key: "test", body: body, images: ["/assets/images/scene.svg"])
      write_post(root, "2026-09-10-test-en.md", lang: "en", key: "test", body: body, images: ["/assets/images/scene.svg"])

      report_out, report_error, report_status = run_validator(root, "--report")
      assert report_status.success?, report_error
      assert_includes report_out, "WARNING"
      assert_includes report_out, "AI 이미지 내부 표기"

      _strict_out, strict_error, strict_status = run_validator(root, "--strict")
      refute strict_status.success?
      assert_includes strict_error, "AI 이미지 내부 표기"
    end
  end

  def test_strict_mode_accepts_a_labeled_svg_and_structural_headings
    with_fixture do |root|
      File.write(File.join(root, "assets", "images", "scene.svg"), "<svg><text>AI로 생성한 이미지</text></svg>")
      body = <<~HTML
        <h2>문장형 소제목.</h2>
        <figure><img src="{{ '/assets/images/scene.svg' | relative_url }}" alt="scene"></figure>
        <h2>한 줄 정리.</h2><p>요약.</p>
        <h2>출처.</h2><ul class="sources"><li><a href="https://example.com">official</a></li></ul>
      HTML
      write_post(root, "2026-09-10-test.md", lang: "ko", key: "test", body: body, images: ["/assets/images/scene.svg"])
      write_post(root, "2026-09-10-test-en.md", lang: "en", key: "test", body: body, images: ["/assets/images/scene.svg"])

      _out, error, status = run_validator(root, "--strict")
      assert status.success?, error
    end
  end

  def test_strict_mode_enforces_later_editorial_rule_versions
    with_fixture do |root|
      File.write(File.join(root, "assets", "images", "scene.svg"), "<svg><text>no label</text></svg>")
      body = <<~HTML
        <h2>문장형 소제목.</h2>
        <figure><img src="{{ '/assets/images/scene.svg' | relative_url }}" alt="scene"></figure>
        <h2>출처.</h2><ul class="sources"><li><a href="https://example.com">official</a></li></ul>
      HTML
      write_post(root, "2026-09-10-test.md", lang: "ko", key: "test", body: body, images: ["/assets/images/scene.svg"], rules_version: 2)

      _out, error, status = run_validator(root, "--strict")
      refute status.success?
      assert_includes error, "AI 이미지 내부 표기"
    end
  end
end
