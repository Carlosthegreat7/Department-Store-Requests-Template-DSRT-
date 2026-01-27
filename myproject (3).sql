-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Jan 27, 2026 at 02:13 AM
-- Server version: 10.4.32-MariaDB
-- PHP Version: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `myproject`
--

-- --------------------------------------------------------

--
-- Table structure for table `age_codes_rds`
--

CREATE TABLE `age_codes_rds` (
  `id` int(11) NOT NULL,
  `age_code` varchar(10) NOT NULL,
  `description` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `age_codes_rds`
--

INSERT INTO `age_codes_rds` (`id`, `age_code`, `description`) VALUES
(1, '22', 'December 2022'),
(2, '23', 'January 2023'),
(3, '24', 'February 2023'),
(4, '25', 'March 2023'),
(5, '26', 'April 2023'),
(6, '27', 'May 2023'),
(7, '28', 'June 2023'),
(8, '29', 'July 2023'),
(9, '30', 'August 2023'),
(10, '31', 'September 2023'),
(11, '32', 'October 2023'),
(12, '33', 'November 2023'),
(13, '34', 'December 2023');

-- --------------------------------------------------------

--
-- Table structure for table `brands`
--

CREATE TABLE `brands` (
  `product_group` varchar(20) NOT NULL,
  `brand_name` varchar(100) NOT NULL,
  `dept_code` varchar(10) NOT NULL,
  `sub_dept_code` varchar(10) NOT NULL,
  `class_code` varchar(10) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `brands`
--

INSERT INTO `brands` (`product_group`, `brand_name`, `dept_code`, `sub_dept_code`, `class_code`) VALUES
('ADIDA', 'ADIDAS', '054', '092', '279'),
('ANNEK', 'ANNE KLEIN', '054', '092', '262'),
('AXIS', 'AXIS', '054', '092', '125'),
('CARL', 'LRAC', '690', '420', '067'),
('DKNY', 'DKNY', '054', '092', '325'),
('ESPRT', 'ESPRIT', '054', '092', '126'),
('FERRA', 'FERRAGAMO', '054', '092', '234'),
('GIORD', 'GIORDANO', '054', '092', '122'),
('GUESS', 'GUESS', '054', '092', '297'),
('GUESSJ', 'GUESS JEWELRY', '054', '091', '322'),
('INGER', 'INGERSOLL', '054', '092', '130'),
('LRAC', 'SOLRACS', '213', '049', '872'),
('NIXON', 'NIXON', '054', '092', '127'),
('POLIS', 'POLICE', '054', '092', '124'),
('SWISS', 'SWISS MILITARY', '054', '092', '257'),
('TIMEX', 'TIMEX', '054', '092', '121'),
('TITAN', 'TITAN', '054', '092', '229'),
('VERSA', 'VERSACE', '054', '092', '235');

-- --------------------------------------------------------

--
-- Table structure for table `hierarchy_rds`
--

CREATE TABLE `hierarchy_rds` (
  `id` int(11) NOT NULL,
  `dept` varchar(10) NOT NULL,
  `sdept` varchar(10) NOT NULL,
  `class` varchar(10) NOT NULL,
  `sclass` varchar(10) NOT NULL,
  `sclass_name` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `hierarchy_rds`
--

INSERT INTO `hierarchy_rds` (`id`, `dept`, `sdept`, `class`, `sclass`, `sclass_name`) VALUES
(1, '710', '400', '409', '-', 'Watches'),
(2, '710', '400', '409', '401', 'Watch Straps'),
(3, '710', '400', '409', '402', 'Watch Batteries'),
(4, '710', '400', '409', '403', 'Watches Women\'s'),
(5, '710', '400', '409', '404', 'Watches Men\'s'),
(6, '710', '400', '409', '405', 'Watches Unisex');

-- --------------------------------------------------------

--
-- Table structure for table `price_points_rds`
--

CREATE TABLE `price_points_rds` (
  `id` int(11) NOT NULL,
  `price_point_code` varchar(10) NOT NULL,
  `price_point_desc` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `price_points_rds`
--

INSERT INTO `price_points_rds` (`id`, `price_point_code`, `price_point_desc`) VALUES
(1, 'NA', 'Not Applicable'),
(2, '01', '1 to 50'),
(3, '02', '51 to 100'),
(4, '03', '101 to 150'),
(5, '04', '151 to 200'),
(6, '05', '201 to 300'),
(7, '06', '301 to 400'),
(8, '07', '401 to 500'),
(9, '08', '501 to 1000'),
(10, '09', '1001 to 2000'),
(11, '10', '2001 to 3000'),
(12, '11', '3001 to 4000'),
(13, '12', '4001 to 5000'),
(14, '13', '5001 to 10000'),
(15, '14', '10001 to 15000'),
(16, '15', '15001 to 20000'),
(17, '16', '20001 and above');

-- --------------------------------------------------------

--
-- Table structure for table `sub_classes`
--

CREATE TABLE `sub_classes` (
  `product_group` varchar(20) NOT NULL,
  `subclass_code` varchar(10) NOT NULL,
  `subclass_name` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `sub_classes`
--

INSERT INTO `sub_classes` (`product_group`, `subclass_code`, `subclass_name`) VALUES
('ADIDA', '013', 'LEATHER'),
('ADIDA', '014', 'STAINLESS'),
('ADIDA', '015', 'PLASTIC'),
('ADIDA', '016', 'RUBBER'),
('ADIDA', '017', 'SETS'),
('ADIDA', '100', 'MD PROMO'),
('ANNEK', '013', 'LEATHER'),
('ANNEK', '014', 'STAINLESS'),
('ANNEK', '015', 'PLASTICK'),
('ANNEK', '016', 'RUBBER'),
('ANNEK', '017', 'SETS'),
('ANNEK', '018', 'STRAP'),
('ANNEK', '100', 'MD PROMO'),
('AXIS', '013', 'LEATHER'),
('AXIS', '014', 'STAINLESS'),
('AXIS', '015', 'PLASTICK'),
('AXIS', '016', 'RUBBER'),
('AXIS', '100', 'MD PROMO'),
('CARL', '431', 'TEST NUMBER DI KO ALAM'),
('DKNY', '013', 'LEATHER'),
('DKNY', '014', 'STAINLESS'),
('DKNY', '016', 'RUBBER'),
('DKNY', '100', 'MD PROMO'),
('ESPRT', '013', 'LEATHER'),
('ESPRT', '014', 'STAINLESS'),
('ESPRT', '015', 'PLASTICK'),
('ESPRT', '016', 'RUBBER'),
('ESPRT', '017', 'SETS'),
('ESPRT', '018', 'STRAP'),
('ESPRT', '100', 'MD PROMO'),
('FERRA', '013', 'LEATHER'),
('FERRA', '014', 'STAINLESS'),
('FERRA', '015', 'PLASTICK'),
('FERRA', '016', 'RUBBER'),
('FERRA', '017', 'SETS'),
('FERRA', '018', 'STRAP'),
('FERRA', '100', 'MD PROMO'),
('GIORD', '013', 'LEATHER'),
('GIORD', '014', 'STAINLESS'),
('GIORD', '015', 'PLASTICK'),
('GIORD', '016', 'RUBBER'),
('GIORD', '100', 'MD PROMO'),
('GUESS', '001', 'LEATHER'),
('GUESS', '002', 'STAINLESS'),
('GUESS', '003', 'RUBBER'),
('GUESS', '100', 'MD PROMO'),
('GUESSJ', '001', 'NECKLACE'),
('GUESSJ', '002', 'EARRINGS'),
('GUESSJ', '003', 'BRACELET'),
('GUESSJ', '004', 'RINGS'),
('GUESSJ', '100', 'SETS'),
('INGER', '013', 'LEATHER'),
('INGER', '014', 'STAINLESS'),
('INGER', '015', 'PLASTICK'),
('INGER', '016', 'RUBBER'),
('INGER', '017', 'SETS'),
('INGER', '018', 'STRAP'),
('INGER', '100', 'MD PROMO'),
('LRAC', '063', 'TESTTESTTESTTESTTESTTEST'),
('NIXON', '013', 'LEATHER'),
('NIXON', '014', 'STAINLESS'),
('NIXON', '015', 'PLASTICK'),
('NIXON', '016', 'RUBBER'),
('NIXON', '100', 'MD PROMO'),
('POLIS', '013', 'LEATHER'),
('POLIS', '014', 'STAINLESS'),
('POLIS', '015', 'PLASTICK'),
('POLIS', '016', 'RUBBER'),
('POLIS', '100', 'MD PROMO'),
('SWISS', '013', 'LEATHER'),
('SWISS', '014', 'STAINLESS'),
('SWISS', '015', 'PLASTICK'),
('SWISS', '016', 'RUBBER'),
('SWISS', '017', 'SETS'),
('SWISS', '018', 'STRAP'),
('SWISS', '100', 'MD PROMO'),
('TIMEX', '013', 'LEATHER'),
('TIMEX', '014', 'STAINLESS'),
('TIMEX', '015', 'PLASTICK'),
('TIMEX', '016', 'RUBBER'),
('TIMEX', '017', 'SETS'),
('TIMEX', '100', 'MD PROMO'),
('TITAN', '013', 'LEATHER'),
('TITAN', '014', 'STAINLESS'),
('TITAN', '015', 'PLASTICK'),
('TITAN', '016', 'RUBBER'),
('TITAN', '017', 'SETS'),
('TITAN', '018', 'STRAP'),
('TITAN', '100', 'MD PROMO'),
('VERSA', '013', 'LEATHER'),
('VERSA', '014', 'STAINLESS'),
('VERSA', '015', 'PLASTICK'),
('VERSA', '016', 'RUBBER'),
('VERSA', '017', 'SETS'),
('VERSA', '018', 'STRAP'),
('VERSA', '100', 'MD PROMO');

-- --------------------------------------------------------

--
-- Table structure for table `vendors`
--

CREATE TABLE `vendors` (
  `vendor_code` varchar(20) NOT NULL,
  `vendor_name` varchar(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `vendors`
--

INSERT INTO `vendors` (`vendor_code`, `vendor_name`) VALUES
('014353', 'ABOUT TIME CORP.'),
('111111', 'CARLOS CORPORAT'),
('120604', 'CARLO CORP.'),
('144011', 'NEWTRENDS INTERNATIONAL CORP.'),
('676767', 'EXAMPLE INC.'),
('694267', 'INC. AH MAGNAYE');

-- --------------------------------------------------------

--
-- Table structure for table `vendors_rds`
--

CREATE TABLE `vendors_rds` (
  `id` int(11) NOT NULL,
  `company_name` varchar(255) NOT NULL,
  `vendor_code` varchar(20) NOT NULL,
  `mfg_part_no` varchar(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `vendors_rds`
--

INSERT INTO `vendors_rds` (`id`, `company_name`, `vendor_code`, `mfg_part_no`) VALUES
(1, 'Newtrends International Corp.', '703921', '7090517'),
(2, 'About Time Corp.', '700194', '7091392');

-- --------------------------------------------------------

--
-- Table structure for table `vendor_chain_mappings`
--

CREATE TABLE `vendor_chain_mappings` (
  `id` int(11) NOT NULL,
  `chain_name` varchar(50) NOT NULL,
  `company_selection` varchar(10) NOT NULL,
  `vendor_code` varchar(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `vendor_chain_mappings`
--

INSERT INTO `vendor_chain_mappings` (`id`, `chain_name`, `company_selection`, `vendor_code`) VALUES
(1, 'SM', 'NIC', '144011'),
(2, 'SM', 'ATC', '014353'),
(3, 'RUSTANS', 'NIC', '703921'),
(4, 'RUSTANS', 'ATC', '700194'),
(5, 'SM', 'CARLOS_COR', '120604'),
(6, 'SM', 'CARLO_CORP', '111111'),
(7, 'SM', 'EXAMPLE', '676767'),
(8, 'SM', 'INC._AH_MA', '694267');

--
-- Indexes for dumped tables
--

--
-- Indexes for table `age_codes_rds`
--
ALTER TABLE `age_codes_rds`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `brands`
--
ALTER TABLE `brands`
  ADD PRIMARY KEY (`product_group`);

--
-- Indexes for table `hierarchy_rds`
--
ALTER TABLE `hierarchy_rds`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `price_points_rds`
--
ALTER TABLE `price_points_rds`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `sub_classes`
--
ALTER TABLE `sub_classes`
  ADD PRIMARY KEY (`product_group`,`subclass_code`);

--
-- Indexes for table `vendors`
--
ALTER TABLE `vendors`
  ADD PRIMARY KEY (`vendor_code`);

--
-- Indexes for table `vendors_rds`
--
ALTER TABLE `vendors_rds`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `vendor_chain_mappings`
--
ALTER TABLE `vendor_chain_mappings`
  ADD PRIMARY KEY (`id`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `age_codes_rds`
--
ALTER TABLE `age_codes_rds`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=14;

--
-- AUTO_INCREMENT for table `hierarchy_rds`
--
ALTER TABLE `hierarchy_rds`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=7;

--
-- AUTO_INCREMENT for table `price_points_rds`
--
ALTER TABLE `price_points_rds`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=18;

--
-- AUTO_INCREMENT for table `vendors_rds`
--
ALTER TABLE `vendors_rds`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=3;

--
-- AUTO_INCREMENT for table `vendor_chain_mappings`
--
ALTER TABLE `vendor_chain_mappings`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=9;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `sub_classes`
--
ALTER TABLE `sub_classes`
  ADD CONSTRAINT `fk_subclass_hierarchy` FOREIGN KEY (`product_group`) REFERENCES `brands` (`product_group`) ON DELETE CASCADE ON UPDATE CASCADE;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
