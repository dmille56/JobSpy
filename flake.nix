{
  description = "JobSpy - Job scraping library for LinkedIn, Indeed, Glassdoor, ZipRecruiter & more";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      poetry2nix,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ poetry2nix.overlays.default ];
        };
        jobspy = pkgs.poetry2nix.mkPoetryEnv {
          projectDir = self;
          preferWheels = true;
        };
        jobspy-dev = pkgs.poetry2nix.mkPoetryEnv {
          projectDir = self;
          preferWheels = true;
          editablePackageSources = {
            jobspy = ./jobspy;
          };
        };
      in
      {
        packages.default = jobspy;

        overlays.default =
          final: prev:
          let
            pkgs = import nixpkgs {
              inherit system;
              overlays = [ poetry2nix.overlays.default ];
            };
          in
          {
            python3Packages = prev.python3Packages // {
              jobspy = pkgs.poetry2nix.mkPoetryEnv {
                projectDir = self;
                preferWheels = true;
              };
            };
          };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ jobspy-dev ];
          LD_LIBRARY_PATH = "${pkgs.openssl.out}/lib:${pkgs.curl.out}/lib";
        };
      }
    );
}
