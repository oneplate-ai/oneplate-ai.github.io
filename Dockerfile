FROM ruby:3.3-slim

WORKDIR /site

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY Gemfile Gemfile.lock* ./
RUN bundle config set --local path /usr/local/bundle \
    && bundle install

COPY . .

EXPOSE 4000
CMD ["bundle", "exec", "jekyll", "serve", "--host", "0.0.0.0", "--port", "4000", "--livereload"]
