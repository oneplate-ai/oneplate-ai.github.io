#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "yaml"

ROOT = File.expand_path(ENV.fetch("ONEPLATE_ROOT", File.expand_path("..", __dir__)))
POSTS_DIR = File.join(ROOT, "_posts")
POST_EXTENSIONS = %w[.md .markdown .html].freeze
AI_DISCLOSURE = "AI로 생성한 이미지"
SOURCE_HEADINGS = ["출처", "sources", "official sources", "공식 출처"].freeze

mode = ARGV.include?("--strict") ? :strict : :report
abort "사용법: ruby scripts/validate_editorial_rules.rb [--report|--strict]" unless (ARGV - ["--report", "--strict"]).empty?


def read_post(path)
  text = File.read(path, encoding: "UTF-8")
  match = text.match(/\A---\s*\n(.*?)\n---(?:\s*\n|\z)/m)
  raise "YAML front matter가 필요합니다." unless match

  metadata = YAML.safe_load(match[1], permitted_classes: [Date, Time], aliases: false) || {}
  raise "front matter는 YAML mapping이어야 합니다." unless metadata.is_a?(Hash)

  [metadata, text.delete_prefix(match[0])]
rescue Psych::Exception => error
  raise "front matter YAML을 읽을 수 없습니다: #{error.message}"
end


def public_post?(metadata)
  metadata.fetch("published", true) != false
end


def plain_text(html)
  html.gsub(/<[^>]+>/, " ").gsub(/\s+/, " ").strip
end


def heading_text(html)
  plain_text(html.gsub(/<a\b[^>]*\bclass=["'][^"']*\bsource-badge\b[^"']*["'][^>]*>.*?<\/a>/im, ""))
end


def source_heading?(heading)
  SOURCE_HEADINGS.include?(heading.downcase.sub(/[.?!…]+\z/, "").strip)
end


def image_paths(body)
  body.scan(%r!/assets/images/[^'"|\s}]+!).uniq
end

warnings = []
checked = 0
strict_checked = 0

Dir.glob(File.join(POSTS_DIR, "*"), File::FNM_CASEFOLD).sort.each do |path|
  next unless File.file?(path) && POST_EXTENSIONS.include?(File.extname(path).downcase)

  begin
    metadata, body = read_post(path)
  rescue StandardError => error
    warnings << "#{File.basename(path)}: #{error.message}"
    next
  end
  next unless public_post?(metadata)

  checked += 1
  strict_candidate = metadata["editorial_rules"].is_a?(Integer) && metadata["editorial_rules"].positive?
  strict_checked += 1 if strict_candidate
  prefix = File.basename(path)

  check = lambda do |message|
    warnings << "#{prefix}: #{message}" if mode == :report || strict_candidate
  end

  check.call("published: true와 noindex: true를 함께 사용할 수 없습니다.") if metadata["noindex"] == true

  headings = body.scan(/<h2\b[^>]*>(.*?)<\/h2>/im).flatten.map { |heading| heading_text(heading) }
  headings.each do |heading|
    next if heading.match?(/[.?!…]\z/)

    check.call("h2 소제목은 마침표·물음표·느낌표로 끝나야 합니다: #{heading}")
  end

  source_blocks = body.scan(/<h2\b[^>]*>(.*?)<\/h2>(.*?)(?=<h2\b|\z)/im)
  sources = source_blocks.select { |heading, _content| source_heading?(plain_text(heading)) }
  if sources.empty?
    check.call("출처 섹션(<h2>출처.</h2> 또는 <h2>Sources.</h2>)이 필요합니다.")
  elsif sources.none? { |_heading, content| content.match?(%r{https?://}i) }
    check.call("출처 섹션에 외부 HTTPS/HTTP 링크가 필요합니다.")
  end

  if body.match?(%r{<figcaption\b[^>]*>\s*(?:AI로 생성한 이미지|AI-generated image)\s*</figcaption>}i)
    check.call("AI 이미지 표기는 figcaption이 아니라 이미지 내부 우측 하단 글씨로만 넣어야 합니다.")
  end

  declared_images = metadata["ai_generated_images"]
  if !declared_images.nil? && !declared_images.is_a?(Array)
    check.call("ai_generated_images는 이미지 경로 배열이어야 합니다.")
    next
  end

  Array(declared_images).each do |image|
    unless image.is_a?(String) && image.start_with?("/assets/images/")
      check.call("ai_generated_images 경로가 올바르지 않습니다: #{image.inspect}")
      next
    end

    asset = File.join(ROOT, image.delete_prefix("/"))
    unless File.file?(asset)
      check.call("선언한 AI 이미지 파일이 없습니다: #{image}")
      next
    end

    unless File.extname(asset).downcase == ".svg"
      check.call("AI 이미지 내부 표기는 현재 SVG만 자동 검증합니다. SVG 래퍼로 교체하세요: #{image}")
      next
    end

    svg = File.read(asset, encoding: "UTF-8")
    check.call("AI 이미지 내부 표기 '#{AI_DISCLOSURE}'가 없습니다: #{image}") unless svg.include?(AI_DISCLOSURE)
  end

  referenced = image_paths(body)
  undeclared = referenced.select { |image| Array(declared_images).include?(image) == false }
  # Existing images may be non-AI assets. Report this as an audit cue only; it is never a strict failure.
  if mode == :report && undeclared.any?
    warnings << "#{prefix}: 이미지 #{undeclared.join(', ')}는 AI 생성 여부 선언이 없어 내부 표기를 자동 확인하지 않았습니다."
  end
end

if warnings.empty?
  puts "편집 규칙 검증 성공: 공개 글 #{checked}편#{mode == :strict ? ", 엄격 검사 #{strict_checked}편" : ""}"
  exit 0
end

if mode == :strict
  warn "편집 규칙 엄격 검증 실패:"
  warnings.each { |warning| warn "- #{warning}" }
  exit 1
end

puts "편집 규칙 보고서: 공개 글 #{checked}편, 경고 #{warnings.size}건 (보고서 모드이므로 차단하지 않음)"
warnings.each { |warning| puts "WARNING: #{warning}" }
