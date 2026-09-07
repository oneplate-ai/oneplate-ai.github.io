#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "rexml/document"
require "uri"
require "yaml"

ROOT = File.expand_path("..", __dir__)
POSTS_DIR = File.join(ROOT, "_posts")
SITE_DIR = File.join(ROOT, "_site")
POST_EXTENSIONS = %w[.md .markdown .html].freeze
SITE_URL = "https://oneplate-ai.github.io"

errors = []

def front_matter(path)
  text = File.read(path, encoding: "UTF-8")
  match = text.match(/\A---\s*\n(.*?)\n---(?:\s*\n|\z)/m)
  raise "#{path}: YAML front matter가 필요합니다." unless match

  raw = YAML.safe_load(match[1], permitted_classes: [Date, Time], aliases: false)
  data = raw.nil? ? {} : raw
  raise "#{path}: front matter는 YAML mapping이어야 합니다." unless data.is_a?(Hash)

  data
rescue Psych::Exception => error
  raise "#{path}: front matter YAML을 읽을 수 없습니다: #{error.message}"
end

def public_post?(data)
  data.fetch("published", true) != false
end

def safe_permalink?(permalink, expected_prefix = nil)
  return false unless permalink.is_a?(String) && permalink.start_with?("/")
  return false if expected_prefix && !permalink.start_with?(expected_prefix)
  return false if permalink.include?("?") || permalink.include?("#")

  segments = URI::DEFAULT_PARSER.unescape(permalink).split("/", -1)
  return false unless segments.first == ""

  path_segments = segments.drop(1)
  path_segments.pop if path_segments.last == ""
  return false if path_segments.empty?

  path_segments.all? { |segment| !segment.empty? && segment != "." && segment != ".." && !segment.include?("\\") }
end

def rendered_path(permalink)
  return nil unless safe_permalink?(permalink)

  relative = permalink.delete_prefix("/")
  relative = "#{relative}index.html" if relative.end_with?("/")
  candidate = File.expand_path(relative, SITE_DIR)
  return nil unless candidate.start_with?("#{SITE_DIR}/")

  candidate
end

def html_attributes(tag)
  tag.scan(/([\w:-]+)=["']([^"']*)["']/).to_h
end

def page_metadata(path)
  html = File.read(path, encoding: "UTF-8")
  links = html.scan(/<link\b[^>]*>/i).map { |tag| html_attributes(tag) }
  canonicals = links.select { |attributes| attributes["rel"] == "canonical" }.map { |attributes| attributes["href"] }
  alternates = links.select { |attributes| attributes["rel"] == "alternate" }
  [canonicals, alternates]
end

def validate_page_metadata(errors, permalink, ko_permalink, en_permalink)
  path = rendered_path(permalink)
  unless path && File.file?(path)
    errors << "생성된 공개 페이지가 없습니다: #{permalink}"
    return
  end

  canonicals, alternates = page_metadata(path)
  expected_canonical = "#{SITE_URL}#{permalink}"
  expected = { "ko-KR" => "#{SITE_URL}#{ko_permalink}", "en" => "#{SITE_URL}#{en_permalink}" }
  errors << "#{permalink}: canonical이 #{expected_canonical}로 정확히 하나여야 합니다." unless canonicals == [expected_canonical]
  expected.each do |hreflang, href|
    matches = alternates.select { |attributes| attributes["hreflang"] == hreflang }.map { |attributes| attributes["href"] }
    errors << "#{permalink}: #{hreflang} hreflang이 #{href}로 정확히 하나여야 합니다." unless matches == [href]
  end
end

posts = []
Dir.children(POSTS_DIR).sort.each do |name|
  path = File.join(POSTS_DIR, name)
  next unless File.file?(path) && POST_EXTENSIONS.include?(File.extname(path))

  begin
    data = front_matter(path)
  rescue StandardError => error
    errors << error.message
    next
  end
  next unless public_post?(data)

  posts << { path: path, data: data }
end

public_permalinks = {}
posts.each do |post|
  permalink = post[:data]["permalink"]
  next unless safe_permalink?(permalink)

  if public_permalinks.key?(permalink)
    errors << "공개 permalink '#{permalink}'가 중복되었습니다: #{public_permalinks[permalink]}와 #{post[:path]}"
  else
    public_permalinks[permalink] = post[:path]
  end
end

pairs = {}
posts.each do |post|
  data = post[:data]
  lang = data["lang"]
  key = data["translation_key"]
  permalink = data["permalink"]
  series = data["series"]

  errors << "#{post[:path]}: 공개 글의 lang은 ko 또는 en이어야 합니다." unless %w[ko en].include?(lang)
  errors << "#{post[:path]}: 공개 글에 translation_key가 필요합니다." unless key.is_a?(String) && !key.empty?
  errors << "#{post[:path]}: 공개 글에 series가 필요합니다." unless series.is_a?(String) && !series.empty?
  expected_prefix = lang == "en" ? "/en/posts/" : "/posts/"
  unless safe_permalink?(permalink, expected_prefix) && rendered_path(permalink)
    errors << "#{post[:path]}: #{lang} 공개 글 permalink는 안전한 #{expected_prefix} URL이어야 합니다."
  end
  next unless %w[ko en].include?(lang) && key.is_a?(String) && !key.empty?

  pairs[key] ||= {}
  errors << "translation_key '#{key}'에 공개 #{lang} 글이 중복되었습니다." if pairs[key].key?(lang)
  pairs[key][lang] = post
end

pairs.each do |key, languages|
  unless languages.keys.sort == %w[en ko]
    errors << "translation_key '#{key}'는 공개 ko·en 번역본을 각각 하나씩 가져야 합니다."
    next
  end

  ko = languages.fetch("ko").fetch(:data)
  en = languages.fetch("en").fetch(:data)
  errors << "translation_key '#{key}'의 ko·en series가 다릅니다." unless ko["series"] == en["series"]
  errors << "translation_key '#{key}'의 ko·en permalink가 같을 수 없습니다." if ko["permalink"] == en["permalink"]
end

archive_urls = []
%w[ko en].each do |lang|
  posts.select { |post| post[:data]["lang"] == lang }.map { |post| post[:data]["series"] }.uniq.each do |series|
    archive_path = lang == "en" ? File.join(ROOT, "en", "series", series, "index.md") : File.join(ROOT, "series", series, "index.md")
    expected_permalink = lang == "en" ? "/en/series/#{series}/" : "/series/#{series}/"
    unless File.file?(archive_path)
      errors << "#{lang} 시리즈 '#{series}'의 아카이브가 없습니다: #{archive_path.delete_prefix("#{ROOT}/")}"
      next
    end

    begin
      archive = front_matter(archive_path)
    rescue StandardError => error
      errors << error.message
      next
    end
    errors << "#{archive_path}: layout은 series여야 합니다." unless archive["layout"] == "series"
    errors << "#{archive_path}: lang이 #{lang}이어야 합니다." unless archive["lang"] == lang
    errors << "#{archive_path}: series가 #{series}이어야 합니다." unless archive["series"] == series
    errors << "#{archive_path}: permalink가 #{expected_permalink}이어야 합니다." unless archive["permalink"] == expected_permalink
    errors << "#{archive_path}: translation_key가 series-#{series}이어야 합니다." unless archive["translation_key"] == "series-#{series}"
    errors << "생성된 아카이브가 없습니다: #{expected_permalink}" unless rendered_path(expected_permalink)&.then { |path| File.file?(path) }
    if public_permalinks.key?(expected_permalink)
      errors << "공개 permalink '#{expected_permalink}'가 중복되었습니다: #{public_permalinks[expected_permalink]}와 #{archive_path}"
    else
      public_permalinks[expected_permalink] = archive_path
    end
    archive_urls << [lang, series, expected_permalink]
  end
end

sitemap_path = File.join(SITE_DIR, "sitemap.xml")
if !File.file?(sitemap_path)
  errors << "생성된 sitemap.xml이 없습니다."
else
  sitemap = REXML::Document.new(File.read(sitemap_path, encoding: "UTF-8"))
  locations = REXML::XPath.match(sitemap, "//xmlns:url/xmlns:loc").map(&:text)
  posts.each do |post|
    expected_url = "#{SITE_URL}#{post[:data]["permalink"]}"
    errors << "sitemap에 #{expected_url}가 정확히 한 번 있어야 합니다." unless locations.count(expected_url) == 1
  end
  archive_urls.each do |_lang, _series, permalink|
    expected_url = "#{SITE_URL}#{permalink}"
    errors << "sitemap에 #{expected_url}가 정확히 한 번 있어야 합니다." unless locations.count(expected_url) == 1
  end
end

pairs.each_value do |languages|
  next unless languages.keys.sort == %w[en ko]

  ko_permalink = languages.fetch("ko").fetch(:data).fetch("permalink")
  en_permalink = languages.fetch("en").fetch(:data).fetch("permalink")
  validate_page_metadata(errors, ko_permalink, ko_permalink, en_permalink)
  validate_page_metadata(errors, en_permalink, ko_permalink, en_permalink)
end

archive_urls.map { |_lang, series, _permalink| series }.uniq.each do |series|
  ko_permalink = "/series/#{series}/"
  en_permalink = "/en/series/#{series}/"
  validate_page_metadata(errors, ko_permalink, ko_permalink, en_permalink)
  validate_page_metadata(errors, en_permalink, ko_permalink, en_permalink)
end

if errors.any?
  warn "다국어 공개 검증 실패:"
  errors.each { |error| warn "- #{error}" }
  exit 1
end

puts "다국어 공개 검증 성공: 공개 번역 쌍 #{pairs.size}개, canonical·hreflang·sitemap·시리즈 아카이브 확인"
